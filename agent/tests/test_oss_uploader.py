"""测试 OSS 上传器（mock OSS 客户端）与 Protocol 一致性。"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent.uploader.chunked import LocalChunkUploader
from agent.uploader.oss import OSS_MIN_PART_SIZE, OSSChunkUploader, OSSConfig
from agent.uploader.protocol import ChunkUploader


class FakeBucket:
    """记录调用的假 bucket，替代真实 OSS。"""

    def __init__(self) -> None:
        self.parts: dict[int, bytes] = {}
        self.completed = False
        self.upload_id = 'fake-upload-id'
        self.fail_parts: set[int] = set()
        self.fail_counts: dict[int, int] = {}

    def init_multipart_upload(self, object_key: str) -> Any:
        return SimpleNamespace(upload_id=self.upload_id)

    def upload_part(self, object_key: str, upload_id: str, part_number: int, chunk: bytes) -> Any:
        if part_number in self.fail_parts:
            self.fail_counts[part_number] = self.fail_counts.get(part_number, 0) + 1
            raise RuntimeError(f'模拟分片 {part_number} 失败')
        self.parts[part_number] = chunk
        return SimpleNamespace(etag=f'etag-{part_number}')

    def complete_multipart_upload(self, object_key: str, upload_id: str, parts: list[Any]) -> Any:
        self.completed = True
        self.completed_parts = [p.part_number for p in parts]
        return SimpleNamespace(etag='final-etag')


def _config() -> OSSConfig:
    return OSSConfig(
        access_key_id='fake-ak',
        access_key_secret='fake-sk',
        endpoint='oss-cn-hangzhou.aliyuncs.com',
        bucket='test-bucket',
    )


class TestOSSConfig:
    """凭据读取。"""

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch):
        """环境变量齐全时正常读取。"""
        for key, value in {
            'OSS_ACCESS_KEY_ID': 'ak',
            'OSS_ACCESS_KEY_SECRET': 'sk',
            'OSS_ENDPOINT': 'oss-cn-hangzhou.aliyuncs.com',
            'OSS_BUCKET': 'my-bucket',
        }.items():
            monkeypatch.setenv(key, value)

        config = OSSConfig.from_env()
        assert config.access_key_id == 'ak'
        assert config.bucket == 'my-bucket'

    def test_missing_credentials_raises(self, monkeypatch: pytest.MonkeyPatch):
        """凭据缺失 → 报错并指明缺哪些，不静默降级。"""
        for key in ('OSS_ACCESS_KEY_ID', 'OSS_ACCESS_KEY_SECRET', 'OSS_ENDPOINT', 'OSS_BUCKET'):
            monkeypatch.delenv(key, raising=False)

        with pytest.raises(RuntimeError, match='OSS 配置缺失') as exc:
            OSSConfig.from_env()

        message = str(exc.value)
        assert 'OSS_ACCESS_KEY_ID' in message
        assert 'OSS_BUCKET' in message

    def test_empty_value_treated_as_missing(self, monkeypatch: pytest.MonkeyPatch):
        """空串等同缺失。"""
        monkeypatch.setenv('OSS_ACCESS_KEY_ID', '')
        monkeypatch.setenv('OSS_ACCESS_KEY_SECRET', 'sk')
        monkeypatch.setenv('OSS_ENDPOINT', 'ep')
        monkeypatch.setenv('OSS_BUCKET', 'b')

        with pytest.raises(RuntimeError, match='OSS_ACCESS_KEY_ID'):
            OSSConfig.from_env()


class TestOSSUpload:
    """分片上传主流程。"""

    def test_uploads_all_parts(self, tmp_path: Path):
        """全部分片上传并 complete。"""
        source = tmp_path / 'ep_001.mcap'
        source.write_bytes(b'x' * (OSS_MIN_PART_SIZE * 3))
        bucket = FakeBucket()
        uploader = OSSChunkUploader(
            _config(), chunk_size=OSS_MIN_PART_SIZE, bucket_client=bucket
        )

        outcome = uploader.upload(source=source, object_key='episodes/ep_001.mcap')

        assert outcome.total_parts == 3
        assert outcome.uploaded_parts == (1, 2, 3)
        assert outcome.complete
        assert bucket.completed
        assert sorted(bucket.parts) == [1, 2, 3]
        assert len(outcome.checksum) == 64

    def test_on_part_done_called_per_part(self, tmp_path: Path):
        """每片完成回调一次（落库的前提）。"""
        source = tmp_path / 'ep_002.mcap'
        source.write_bytes(b'y' * (OSS_MIN_PART_SIZE * 2))
        bucket = FakeBucket()
        uploader = OSSChunkUploader(
            _config(), chunk_size=OSS_MIN_PART_SIZE, bucket_client=bucket
        )

        seen: list[int] = []
        uploader.upload(
            source=source, object_key='k', on_part_done=lambda p: seen.append(p)
        )

        assert seen == [1, 2]

    def test_resume_skips_uploaded_parts(self, tmp_path: Path):
        """续传跳过已完成分片，不重传。"""
        source = tmp_path / 'ep_003.mcap'
        source.write_bytes(b'z' * (OSS_MIN_PART_SIZE * 4))
        bucket = FakeBucket()
        # 续传需要列已有分片，FakeBucket 不实现 PartIterator，因此直接给 parts
        uploader = OSSChunkUploader(
            _config(), chunk_size=OSS_MIN_PART_SIZE, bucket_client=bucket
        )
        uploader._list_existing_parts = lambda k, u: [  # type: ignore[method-assign]
            SimpleNamespace(part_number=n, etag=f'etag-{n}') for n in (1, 2)
        ]

        seen: list[int] = []
        outcome = uploader.upload(
            source=source,
            object_key='k',
            already_uploaded=(1, 2),
            on_part_done=lambda p: seen.append(p),
            upload_id='existing-id',
        )

        # 只上传 3、4
        assert seen == [3, 4]
        assert sorted(bucket.parts) == [3, 4]
        assert outcome.uploaded_parts == (1, 2, 3, 4)

    def test_part_retried_then_succeeds(self, tmp_path: Path):
        """单片失败后重试（其他分片进度不受影响）。"""
        source = tmp_path / 'ep_004.mcap'
        source.write_bytes(b'w' * (OSS_MIN_PART_SIZE * 2))
        bucket = FakeBucket()

        # 第 1 片先失败两次再成功
        original = bucket.upload_part
        attempts = {'n': 0}

        def flaky(object_key: str, upload_id: str, part_number: int, chunk: bytes) -> Any:
            if part_number == 1:
                attempts['n'] += 1
                if attempts['n'] <= 2:
                    raise RuntimeError('模拟网络故障')
            return original(object_key, upload_id, part_number, chunk)

        bucket.upload_part = flaky  # type: ignore[method-assign]
        uploader = OSSChunkUploader(
            _config(), chunk_size=OSS_MIN_PART_SIZE, max_retries=3, bucket_client=bucket
        )

        outcome = uploader.upload(source=source, object_key='k')

        assert outcome.complete
        assert attempts['n'] == 3  # 失败两次，第三次成功

    def test_retry_exhausted_raises(self, tmp_path: Path):
        """重试耗尽 → 报错，已完成分片状态保留供续传。"""
        source = tmp_path / 'ep_005.mcap'
        source.write_bytes(b'v' * (OSS_MIN_PART_SIZE * 2))
        bucket = FakeBucket()
        bucket.fail_parts = {2}
        uploader = OSSChunkUploader(
            _config(), chunk_size=OSS_MIN_PART_SIZE, max_retries=2, bucket_client=bucket
        )

        seen: list[int] = []
        with pytest.raises(RuntimeError, match='重试耗尽'):
            uploader.upload(source=source, object_key='k', on_part_done=lambda p: seen.append(p))

        # 第 1 片已落库，可据此续传
        assert seen == [1]
        assert not bucket.completed
        assert bucket.fail_counts[2] == 3  # max_retries=2 → 共 3 次尝试

    def test_chunk_size_floor(self, tmp_path: Path):
        """小于 OSS 下限的 chunk_size 被抬到下限。"""
        uploader = OSSChunkUploader(_config(), chunk_size=1024, bucket_client=FakeBucket())
        assert uploader._chunk_size == OSS_MIN_PART_SIZE


class TestProtocolConformance:
    """本地与 OSS 实现满足同一接口。"""

    def test_local_satisfies_protocol(self, tmp_path: Path):
        local = LocalChunkUploader(object_store_root=tmp_path, chunk_size=1024)
        assert isinstance(local, ChunkUploader)

    def test_oss_satisfies_protocol(self):
        assert isinstance(OSSChunkUploader(_config(), bucket_client=FakeBucket()), ChunkUploader)

    def test_same_outcome_shape(self, tmp_path: Path):
        """两种实现产出同样结构的 UploadOutcome，调用方不感知差异。"""
        source = tmp_path / 'ep.mcap'
        source.write_bytes(b'q' * (OSS_MIN_PART_SIZE * 2))

        local = LocalChunkUploader(
            object_store_root=tmp_path / 'store', chunk_size=OSS_MIN_PART_SIZE
        )
        oss = OSSChunkUploader(
            _config(), chunk_size=OSS_MIN_PART_SIZE, bucket_client=FakeBucket()
        )

        local_outcome = local.upload(source=source, object_key='k1')
        oss_outcome = oss.upload(source=source, object_key='k2')

        assert local_outcome.total_parts == oss_outcome.total_parts
        assert local_outcome.uploaded_parts == oss_outcome.uploaded_parts
        assert local_outcome.size_bytes == oss_outcome.size_bytes
        # checksum 都是源文件的 SHA-256
        assert local_outcome.checksum == oss_outcome.checksum

"""契约模型基类。"""

from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    """所有契约模型的基类。

    - ``frozen=True``：不可变，变更用 ``model_copy(update=...)`` 返回新对象
    - ``extra="forbid"``：拒绝未声明字段，让契约漂移在边界处立即暴露
    - ``use_enum_values=False``：保留枚举类型，避免下游拿到裸字符串
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        use_enum_values=False,
        validate_assignment=True,
        str_strip_whitespace=True,
    )


__all__ = ["ContractModel"]

/**
 * 停留时长格式化。
 *
 * 时长不存字段，由轨迹里相邻两条记录的时间差推导（design.md 第 7 节）——
 * 存了就有和时间戳不一致的风险。
 */

/** `3秒` / `2分12秒` / `1小时5分` —— 逐级降精度，长的不显示秒。 */
export function formatDuration(ms: number): string {
  if (ms < 1000) return "不足1秒";

  const totalSeconds = Math.floor(ms / 1000);
  const seconds = totalSeconds % 60;
  const totalMinutes = Math.floor(totalSeconds / 60);
  const minutes = totalMinutes % 60;
  const hours = Math.floor(totalMinutes / 60);

  if (hours > 0) return minutes > 0 ? `${hours}小时${minutes}分` : `${hours}小时`;
  if (totalMinutes > 0)
    return seconds > 0 ? `${totalMinutes}分${seconds}秒` : `${totalMinutes}分`;
  return `${seconds}秒`;
}

/** 两个 ISO 时刻之间的时长。 */
export function durationBetween(fromIso: string, toIso: string): string {
  return formatDuration(
    new Date(toIso).getTime() - new Date(fromIso).getTime(),
  );
}

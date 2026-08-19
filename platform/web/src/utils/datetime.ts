/**
 * 时间显示统一走这里。
 *
 * 后端返回的是带偏移标记的 UTC（`...Z`），显示时固定换算到北京时间 ——
 * 不跟随浏览器所在时区，避免同一条数据在不同机器上显示成不同时刻。
 */

const BEIJING = "Asia/Shanghai";

/** `08-19 10:57` —— 表格里用，省掉年份省空间。 */
export function formatShort(iso: string): string {
  return new Date(iso).toLocaleString("zh-CN", {
    timeZone: BEIJING,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

/** `2026-08-19 10:57:45` —— tooltip 与详情里用。 */
export function formatFull(iso: string): string {
  return new Date(iso).toLocaleString("zh-CN", {
    timeZone: BEIJING,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

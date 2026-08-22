export const PUBLICATION_STATUS = Object.freeze({
  pending_approval: { label: "待审核", tone: "warning" },
  scheduled: { label: "已计划", tone: "scheduled" },
  queued: { label: "等待执行", tone: "progress" },
  submitting: { label: "正在发布", tone: "progress" },
  published: { label: "发布成功", tone: "success" },
  failed: { label: "发布失败", tone: "danger" },
  submission_unknown: { label: "结果待核对", tone: "danger" },
  requires_reauth: { label: "需要重新授权", tone: "danger" },
  cancelled: { label: "已取消", tone: "muted" },
});

export const PUBLICATION_STATUS_FILTERS = Object.freeze([
  ["", "全部状态"],
  ...Object.entries(PUBLICATION_STATUS).map(([value, { label }]) => [value, label]),
]);

export const PUBLISHING_TIME_ZONES = Object.freeze([
  "Asia/Shanghai",
  "Asia/Hong_Kong",
  "Asia/Tokyo",
  "America/Los_Angeles",
  "Europe/London",
]);

export function collectionItems(payload) {
  if (Array.isArray(payload)) return payload;
  return Array.isArray(payload?.items) ? payload.items : [];
}

export function publicationStatus(value) {
  return PUBLICATION_STATUS[value] || {
    label: value ? `未知状态：${value}` : "状态未记录",
    tone: "muted",
  };
}

export function defaultPublishingTimeZone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai";
  } catch {
    return "Asia/Shanghai";
  }
}

export function instantToLocalSchedule(value, timeZone) {
  const date = value instanceof Date ? new Date(value.getTime()) : new Date(value);
  if (!Number.isFinite(date.getTime())) throw new Error("请选择有效的发布时间");
  try {
    const parts = partsInTimeZone(date, timeZone);
    return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
  } catch {
    throw new Error("所选时区无效，请重新选择");
  }
}

function partsInTimeZone(date, timeZone) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  return Object.fromEntries(
    parts.filter(({ type }) => type !== "literal").map(({ type, value }) => [type, value]),
  );
}

function timeZoneOffsetMinutes(date, timeZone) {
  const parts = partsInTimeZone(date, timeZone);
  const representedAsUtc = Date.UTC(
    Number(parts.year),
    Number(parts.month) - 1,
    Number(parts.day),
    Number(parts.hour),
    Number(parts.minute),
    Number(parts.second),
  );
  return Math.round((representedAsUtc - date.getTime()) / 60_000);
}

function formatOffset(minutes) {
  const sign = minutes >= 0 ? "+" : "-";
  const absolute = Math.abs(minutes);
  return `${sign}${String(Math.floor(absolute / 60)).padStart(2, "0")}:${String(absolute % 60).padStart(2, "0")}`;
}

export function localScheduleToOffsetIso(value, timeZone) {
  const match = String(value || "").match(
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/,
  );
  if (!match) throw new Error("请选择有效的发布时间");
  const [, year, month, day, hour, minute, second = "00"] = match;
  const desired = {
    year: Number(year),
    month: Number(month),
    day: Number(day),
    hour: Number(hour),
    minute: Number(minute),
    second: Number(second),
  };
  const wallClockUtc = Date.UTC(
    desired.year,
    desired.month - 1,
    desired.day,
    desired.hour,
    desired.minute,
    desired.second,
  );
  const normalized = new Date(wallClockUtc);
  if (
    normalized.getUTCFullYear() !== desired.year ||
    normalized.getUTCMonth() + 1 !== desired.month ||
    normalized.getUTCDate() !== desired.day ||
    normalized.getUTCHours() !== desired.hour ||
    normalized.getUTCMinutes() !== desired.minute ||
    normalized.getUTCSeconds() !== desired.second
  ) {
    throw new Error("请选择有效的发布时间");
  }

  const sameWallClock = (parts) => (
    Number(parts.year) === desired.year &&
    Number(parts.month) === desired.month &&
    Number(parts.day) === desired.day &&
    Number(parts.hour) === desired.hour &&
    Number(parts.minute) === desired.minute &&
    Number(parts.second) === desired.second
  );

  const possibleOffsets = new Set();
  try {
    // Sample both sides of any nearby DST transition. Candidate instants are
    // accepted only after an exact wall-clock round trip in the selected zone.
    for (let hours = -48; hours <= 48; hours += 6) {
      possibleOffsets.add(
        timeZoneOffsetMinutes(
          new Date(wallClockUtc + hours * 60 * 60_000),
          timeZone,
        ),
      );
    }
  } catch {
    throw new Error("所选时区无效，请重新选择");
  }

  const candidates = [...possibleOffsets]
    .map((offset) => ({
      offset,
      instant: new Date(wallClockUtc - offset * 60_000),
    }))
    .filter(({ offset, instant }) => (
      sameWallClock(partsInTimeZone(instant, timeZone)) &&
      timeZoneOffsetMinutes(instant, timeZone) === offset
    ));
  const uniqueCandidates = [
    ...new Map(candidates.map((candidate) => [candidate.instant.getTime(), candidate])).values(),
  ];

  if (uniqueCandidates.length === 0) {
    throw new Error("所选时区不存在这个本地时间，请避开夏令时跳转时段");
  }
  if (uniqueCandidates.length > 1) {
    throw new Error("所选时区的本地时间存在两个可能时刻，请避开夏令时回拨时段");
  }

  const [{ instant, offset }] = uniqueCandidates;
  if (!sameWallClock(partsInTimeZone(instant, timeZone))) {
    throw new Error("发布时间与所选时区无法一致，请重新选择");
  }
  return `${year}-${month}-${day}T${hour}:${minute}:${second}${formatOffset(offset)}`;
}

export function publicationActionAvailability(status) {
  return {
    approve: status === "pending_approval",
    cancel: ["pending_approval", "scheduled", "queued"].includes(status),
    retry: ["failed", "requires_reauth"].includes(status),
  };
}

export function publicationArtifactId(artwork) {
  return String(artwork?.artifact_id || artwork?.id || "").trim();
}

export function publicationJobArtifactId(job) {
  return String(job?.task_artifact_id || job?.artifact_id || "").trim();
}

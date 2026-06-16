const SEOUL_TIME_ZONE = 'Asia/Seoul';

function parseDate(value) {
  if (!value) return null;
  let normalized = value;
  if (typeof value === 'string' && /T\d{2}:\d{2}/.test(value) && !/(Z|[+-]\d{2}:\d{2})$/.test(value)) {
    normalized = `${value}Z`;
  }
  const date = normalized instanceof Date ? normalized : new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDateTimeSeoul(value) {
  const date = parseDate(value);
  if (!date) return '-';
  return date.toLocaleString('ko-KR', {
    timeZone: SEOUL_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

export function formatDateSeoul(value) {
  const date = parseDate(value);
  if (!date) return '-';
  return date.toLocaleDateString('ko-KR', {
    timeZone: SEOUL_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  });
}

export function formatTimeSeoul(value = new Date()) {
  const date = parseDate(value);
  if (!date) return '-';
  return date.toLocaleTimeString('ko-KR', {
    timeZone: SEOUL_TIME_ZONE,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

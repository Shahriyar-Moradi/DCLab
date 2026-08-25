const currencyFormatters = new Map<string, Intl.NumberFormat>();

export function formatMoney(amount: number, currency = "AED"): string {
  let formatter = currencyFormatters.get(currency);
  if (!formatter) {
    formatter = new Intl.NumberFormat("en-AE", {
      style: "currency",
      currency,
      maximumFractionDigits: 0,
    });
    currencyFormatters.set(currency, formatter);
  }
  return formatter.format(amount);
}

export function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

export function formatTimestamp(value: string | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

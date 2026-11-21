const eurFormatter = new Intl.NumberFormat("en-GB", { style: "currency", currency: "EUR" });
const eurWhole = new Intl.NumberFormat("en-GB", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });

/** 3800 → "€3,800.00", −22097.61 → "−€22,097.61" */
export const formatEur = (value: number) => eurFormatter.format(value).replace("-", "−");

/** Whole euros for headlines and cards: −22097.61 → "−€22,098" */
export const formatEurWhole = (value: number) => eurWhole.format(value).replace("-", "−");

/** Compact, for chart axes: 3800 → "€3.8k", −22000 → "−€22k", 0 → "€0" */
export const formatEurShort = (value: number) => {
  const abs = Math.abs(value);
  const sign = value < 0 ? "−" : "";
  if (abs < 1000) return `${sign}€${Math.round(abs)}`;
  const k = abs / 1000;
  return `${sign}€${k >= 10 || Number.isInteger(k) ? Math.round(k) : k.toFixed(1)}k`;
};

/** One decimal in thousands for ranges: −24363 → "−€24.4k", 950 → "€950". */
export const formatEurK1 = (value: number) => {
  const abs = Math.abs(value);
  const sign = value < 0 ? "−" : "";
  return abs < 1000 ? `${sign}€${Math.round(abs)}` : `${sign}€${(abs / 1000).toFixed(1)}k`;
};

/** "2025-04-14" → "14 Apr" */
export const formatDate = (iso: string) =>
  new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });

/** "2025-04-14" → "14 April 2025" */
export const formatLongDate = (iso: string) =>
  new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });

export const formatDateTime = (iso: string) =>
  new Date(iso).toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });

export const shortHash = (hash: string) => hash.slice(0, 8);

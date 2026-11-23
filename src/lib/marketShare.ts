import type { MarketShareBrand, MarketShareResponse } from "./api";

const SHARE_SIGNALS = ["sales", "app", "conversation", "engagement"] as const;

export function observedShare(
  brand: MarketShareBrand,
  weights: MarketShareResponse["model"]["active_weights"],
): number | null {
  return SHARE_SIGNALS.some((key) => weights[key] > 0 && brand.signals[key] > 0)
    ? brand.share
    : null;
}

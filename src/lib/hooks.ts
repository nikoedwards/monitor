import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  qs,
  type Brand,
  type Link,
  type Product,
  type RecordItem,
  type SalesMetric,
  type Source,
  type VocAction,
  type WebMonitor,
  type WebAiAnalysis,
  type WebSnapshot,
  type WebSummary,
} from "./api";
import { rangeParams, type TimeRange } from "./timeRange";

// ---------------------------------------------------------------- brands
export function useBrands() {
  return useQuery({
    queryKey: ["brands"],
    queryFn: () => api.get<{ brands: Brand[] }>("/api/brands").then((d) => d.brands),
  });
}

export function useBrandMutations() {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ["brands"] });
  return {
    create: useMutation({ mutationFn: (b: Partial<Brand>) => api.post<Brand>("/api/brands", b), onSuccess: invalidate }),
    update: useMutation({
      mutationFn: ({ id, ...b }: Partial<Brand> & { id: string }) => api.put<Brand>(`/api/brands/${id}`, b),
      onSuccess: invalidate,
    }),
    remove: useMutation({ mutationFn: (id: string) => api.del(`/api/brands/${id}`), onSuccess: invalidate }),
    analyze: useMutation({ mutationFn: (url: string) => api.post<Partial<Brand>>("/api/brands/analyze", { url }) }),
  };
}

// ---------------------------------------------------------------- products / links
export function useProducts(brandId?: string) {
  return useQuery({
    queryKey: ["products", brandId],
    queryFn: () => api.get<{ products: Product[] }>(`/api/brands/${brandId}/products`).then((d) => d.products),
    enabled: !!brandId,
  });
}

export function useLinks(brandId?: string, dimension?: string, channel?: string) {
  return useQuery({
    queryKey: ["links", brandId, dimension, channel],
    queryFn: () => api.get<{ links: Link[] }>(`/api/links${qs({ brand_id: brandId, dimension, channel })}`).then((d) => d.links),
    enabled: !!brandId,
  });
}

export function useCatalogMutations() {
  const qc = useQueryClient();
  return {
    addProduct: useMutation({
      mutationFn: (p: Partial<Product>) => api.post<Product>("/api/products", p),
      onSuccess: () => qc.invalidateQueries({ queryKey: ["products"] }),
    }),
    delProduct: useMutation({
      mutationFn: (id: string) => api.del(`/api/products/${id}`),
      onSuccess: () => qc.invalidateQueries({ queryKey: ["products"] }),
    }),
    addLink: useMutation({
      mutationFn: (l: Partial<Link>) => api.post<Link>("/api/links", l),
      onSuccess: () => qc.invalidateQueries({ queryKey: ["links"] }),
    }),
    updateLink: useMutation({
      mutationFn: ({ id, ...l }: Partial<Link> & { id: string }) => api.put<Link>(`/api/links/${id}`, l),
      onSuccess: () => qc.invalidateQueries({ queryKey: ["links"] }),
    }),
    delLink: useMutation({
      mutationFn: (id: string) => api.del(`/api/links/${id}`),
      onSuccess: () => qc.invalidateQueries({ queryKey: ["links"] }),
    }),
  };
}

// ---------------------------------------------------------------- settings / AI
export function useSettings() {
  return useQuery({ queryKey: ["settings"], queryFn: () => api.get<import("./api").LlmSettings>("/api/settings") });
}

export function useSaveSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (s: Record<string, unknown>) => api.put("/api/settings", s),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }),
  });
}

export function useAiDraft() {
  return useMutation({
    mutationFn: (keyword: string) => api.post<{ draft: import("./api").BrandDraft }>("/api/brands/ai-draft", { keyword }).then((d) => d.draft),
  });
}

export function useCreateFromDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: Record<string, unknown>) => api.post("/api/brands/from-draft", payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["brands"] }),
  });
}

// ---------------------------------------------------------------- records
export function useRecords(filters: Record<string, unknown>) {
  return useQuery({
    queryKey: ["records", filters],
    queryFn: () => api.get<{ records: RecordItem[] }>(`/api/records${qs(filters)}`).then((d) => d.records),
  });
}

// ---------------------------------------------------------------- overview / compare
export function useOverview(brandId?: string, range?: TimeRange) {
  const rp = rangeParams(range);
  return useQuery({
    queryKey: ["overview", brandId, rp],
    queryFn: () => api.get<any>(`/api/overview${qs({ brand_id: brandId, ...rp })}`),
    enabled: !!brandId,
  });
}

export function useCompare(range?: TimeRange) {
  const rp = rangeParams(range);
  return useQuery({
    queryKey: ["compare", rp],
    queryFn: () => api.get<{ brands: any[] }>(`/api/compare${qs({ ...rp })}`).then((d) => d.brands),
  });
}

// ---------------------------------------------------------------- voc
export function useVocSummary(brandId?: string, range?: TimeRange) {
  const rp = rangeParams(range);
  return useQuery({
    queryKey: ["voc-summary", brandId, rp],
    queryFn: () => api.get<any>(`/api/voc/summary${qs({ brand_id: brandId, ...rp })}`),
    enabled: !!brandId,
  });
}

export function useVocActions(brandId?: string) {
  return useQuery({
    queryKey: ["voc-actions", brandId],
    queryFn: () => api.get<{ actions: VocAction[] }>(`/api/voc/actions${qs({ brand_id: brandId })}`).then((d) => d.actions),
    enabled: !!brandId,
  });
}

export function useVocMutations() {
  const qc = useQueryClient();
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["voc-actions"] });
    qc.invalidateQueries({ queryKey: ["voc-summary"] });
  };
  return {
    addRecord: useMutation({
      mutationFn: (r: Record<string, unknown>) => api.post("/api/records", r),
      onSuccess: () => {
        qc.invalidateQueries({ queryKey: ["records"] });
        qc.invalidateQueries({ queryKey: ["voc-summary"] });
      },
    }),
    importRows: useMutation({
      mutationFn: (payload: Record<string, unknown>) => api.post("/api/import", payload),
      onSuccess: () => {
        qc.invalidateQueries({ queryKey: ["records"] });
        qc.invalidateQueries({ queryKey: ["voc-summary"] });
      },
    }),
    addAction: useMutation({ mutationFn: (a: Partial<VocAction>) => api.post("/api/voc/actions", a), onSuccess: invalidate }),
    updateAction: useMutation({
      mutationFn: ({ id, ...a }: Partial<VocAction> & { id: string }) => api.put(`/api/voc/actions/${id}`, a),
      onSuccess: invalidate,
    }),
    delAction: useMutation({ mutationFn: (id: string) => api.del(`/api/voc/actions/${id}`), onSuccess: invalidate }),
  };
}

// ---------------------------------------------------------------- marketing
export function useMarketingSummary(brandId?: string, channel?: string, range?: TimeRange) {
  const rp = rangeParams(range);
  return useQuery({
    queryKey: ["marketing-summary", brandId, channel, rp],
    queryFn: () => api.get<any>(`/api/marketing/summary${qs({ brand_id: brandId, channel, ...rp })}`),
    enabled: !!brandId,
  });
}

// ---------------------------------------------------------------- insights summary (LLM)
export function useInsightsSummary() {
  return useMutation({
    mutationFn: ({ brandId, dimension, channel, range }: { brandId: string; dimension?: string; channel?: string; range?: TimeRange }) =>
      api.get<any>(`/api/insights/summary${qs({ brand_id: brandId, dimension, channel, ...rangeParams(range) })}`),
  });
}

// ---------------------------------------------------------------- creators
export function useCreatorsSummary(brandId?: string, platform?: string, range?: TimeRange) {
  const rp = rangeParams(range);
  return useQuery({
    queryKey: ["creators-summary", brandId, platform, rp],
    queryFn: () => api.get<any>(`/api/creators/summary${qs({ brand_id: brandId, platform, ...rp })}`),
    enabled: !!brandId,
  });
}

export function useCreatorsRoster(brandId?: string, platform?: string) {
  return useQuery({
    queryKey: ["creators-roster", brandId, platform],
    queryFn: () =>
      api
        .get<{ roster: import("./api").CreatorRosterItem[] }>(`/api/creators/roster${qs({ brand_id: brandId, platform })}`)
        .then((d) => d.roster),
    enabled: !!brandId,
  });
}

export function useCreatorsSync() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ brandId, platform }: { brandId: string; platform?: string }) =>
      api.post<any>(`/api/creators/sync${qs({ brand_id: brandId, platform })}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["creators-summary"] });
      qc.invalidateQueries({ queryKey: ["creators-roster"] });
      qc.invalidateQueries({ queryKey: ["records"] });
    },
  });
}

export function useCreatorsReport() {
  return useMutation({
    mutationFn: (brandId: string) => api.get<{ report: string }>(`/api/creators/report${qs({ brand_id: brandId })}`).then((d) => d.report),
  });
}

// ---------------------------------------------------------------- sales
export function useSalesSummary(brandId?: string, productId?: string, range?: TimeRange) {
  const rp = rangeParams(range);
  return useQuery({
    queryKey: ["sales-summary", brandId, productId, rp],
    queryFn: () => api.get<any>(`/api/sales/summary${qs({ brand_id: brandId, product_id: productId, ...rp })}`),
    enabled: !!brandId,
  });
}

export function useSalesMetrics(brandId?: string, channel?: string, productId?: string) {
  return useQuery({
    queryKey: ["sales-metrics", brandId, channel, productId],
    queryFn: () => api.get<{ metrics: SalesMetric[] }>(`/api/sales/metrics${qs({ brand_id: brandId, channel, product_id: productId })}`).then((d) => d.metrics),
    enabled: !!brandId,
  });
}

export function useSalesListings(brandId?: string, channel?: string, productId?: string) {
  return useQuery({
    queryKey: ["sales-listings", brandId, channel, productId],
    queryFn: () =>
      api
        .get<{ listings: import("./api").SalesListing[] }>(`/api/sales/listings${qs({ brand_id: brandId, channel: channel === "all" ? undefined : channel, product_id: productId })}`)
        .then((d) => d.listings),
    enabled: !!brandId,
  });
}

export function useListingHistory(listingId?: string) {
  return useQuery({
    queryKey: ["sales-listing-history", listingId],
    queryFn: () => api.get<any>(`/api/sales/listings/${listingId}/history`),
    enabled: !!listingId,
  });
}

function invalidateSales(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: ["sales-summary"] });
  qc.invalidateQueries({ queryKey: ["sales-metrics"] });
  qc.invalidateQueries({ queryKey: ["sales-listings"] });
}

export function useSalesMutations() {
  const qc = useQueryClient();
  const invalidate = () => invalidateSales(qc);
  return {
    add: useMutation({ mutationFn: (m: Partial<SalesMetric>) => api.post("/api/sales/metrics", m), onSuccess: invalidate }),
    remove: useMutation({ mutationFn: (id: string) => api.del(`/api/sales/metrics/${id}`), onSuccess: invalidate }),
  };
}

export function useSalesSync() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ brandId, linkId }: { brandId: string; linkId?: string }) =>
      api.post<any>(`/api/sales/sync${qs({ brand_id: brandId, link_id: linkId })}`),
    onSuccess: () => invalidateSales(qc),
  });
}

export function useListingMutations() {
  const qc = useQueryClient();
  const invalidate = () => invalidateSales(qc);
  return {
    update: useMutation({
      mutationFn: ({ id, ...patch }: { id: string; monitor?: boolean; status?: string; product_id?: string | null }) =>
        api.put(`/api/sales/listings/${id}`, patch),
      onSuccess: invalidate,
    }),
    remove: useMutation({ mutationFn: (id: string) => api.del(`/api/sales/listings/${id}`), onSuccess: invalidate }),
  };
}

export interface AutomapResult {
  candidates: number;
  mapped: number;
  results: { listing_id: string; product_id?: string | null; product_name?: string; confidence?: number | null; reason?: string; applied: boolean }[];
}

export function useListingAutomap() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ brandId, channel }: { brandId: string; channel?: string }) =>
      api.post<AutomapResult>(`/api/sales/listings/automap${qs({ brand_id: brandId, channel: channel === "all" ? undefined : channel })}`),
    onSuccess: () => invalidateSales(qc),
  });
}

// ---------------------------------------------------------------- web
export function useWebMonitors(brandId?: string) {
  return useQuery({
    queryKey: ["web-monitors", brandId],
    queryFn: () => api.get<{ monitors: WebMonitor[] }>(`/api/web/monitors${qs({ brand_id: brandId })}`).then((d) => d.monitors),
    enabled: !!brandId,
    refetchInterval: 60_000,
  });
}

export function useWebSnapshots(brandId?: string, monitorId?: string, range?: TimeRange) {
  const rp = rangeParams(range);
  return useQuery({
    queryKey: ["web-snapshots", brandId, monitorId, rp],
    queryFn: () => api.get<{ snapshots: WebSnapshot[] }>(`/api/web/snapshots${qs({ brand_id: brandId, monitor_id: monitorId, ...rp })}`).then((d) => d.snapshots),
    enabled: !!brandId,
  });
}

export function useWebSnapshotHistory(brandId?: string, monitorId?: string, enabled = false) {
  return useQuery({
    queryKey: ["web-snapshot-history", brandId, monitorId],
    queryFn: () => api.get<{ snapshots: WebSnapshot[] }>(`/api/web/snapshots${qs({ brand_id: brandId, monitor_id: monitorId, days: 36500 })}`).then((d) => d.snapshots),
    enabled: enabled && !!brandId,
  });
}

export function useWebSummary(brandId?: string, monitorId?: string, range?: TimeRange) {
  const rp = rangeParams(range);
  return useQuery({
    queryKey: ["web-summary", brandId, monitorId, rp],
    queryFn: () => api.get<WebSummary>(`/api/web/summary${qs({ brand_id: brandId, monitor_id: monitorId, ...rp })}`),
    enabled: !!brandId,
  });
}

export function useWebAnalysis() {
  return useMutation({
    mutationFn: ({ brandId, monitorId, range, refresh = false }: { brandId: string; monitorId?: string; range: TimeRange; refresh?: boolean }) =>
      api.post<WebAiAnalysis>(`/api/web/analyze${qs({ brand_id: brandId, monitor_id: monitorId, refresh, ...rangeParams(range) })}`),
  });
}

export function useDeleteWebSnapshot() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.del<{ deleted: string; monitor_id?: string }>(`/api/web/snapshots/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["web-monitors"] });
      qc.invalidateQueries({ queryKey: ["web-snapshots"] });
      qc.invalidateQueries({ queryKey: ["web-snapshot-history"] });
      qc.invalidateQueries({ queryKey: ["web-summary"] });
    },
  });
}

export function useWebMutations() {
  const qc = useQueryClient();
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["web-monitors"] });
    qc.invalidateQueries({ queryKey: ["web-snapshots"] });
    qc.invalidateQueries({ queryKey: ["web-summary"] });
  };
  return {
    create: useMutation({ mutationFn: (m: Record<string, unknown>) => api.post("/api/web/monitors", m), onSuccess: invalidate }),
    capture: useMutation({ mutationFn: (id: string) => api.post(`/api/web/monitors/${id}/capture`), onSuccess: invalidate }),
    update: useMutation({
      mutationFn: ({ id, ...m }: Record<string, unknown> & { id: string }) => api.put(`/api/web/monitors/${id}`, m),
      onSuccess: invalidate,
    }),
    remove: useMutation({ mutationFn: (id: string) => api.del(`/api/web/monitors/${id}`), onSuccess: invalidate }),
  };
}

// ---------------------------------------------------------------- monitoring status
export function useMonitoringStatus(brandId: string | undefined, dimension: "marketing" | "sales") {
  return useQuery({
    queryKey: ["monitoring-status", brandId, dimension],
    queryFn: () => api.get<any>(`/api/sources/status${qs({ brand_id: brandId, dimension })}`),
    enabled: !!brandId,
    refetchInterval: 60_000,
  });
}

export function useMonitoringRefresh() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ brandId, dimension }: { brandId: string; dimension: "marketing" | "sales" }) =>
      api.post<any>(`/api/sources/refresh${qs({ brand_id: brandId, dimension })}`),
    onSuccess: (_d, vars) => {
      qc.invalidateQueries({ queryKey: ["monitoring-status"] });
      qc.invalidateQueries({ queryKey: ["sources"] });
      qc.invalidateQueries({ queryKey: ["records"] });
      qc.invalidateQueries({ queryKey: ["overview"] });
      if (vars.dimension === "marketing") {
        qc.invalidateQueries({ queryKey: ["marketing-summary"] });
      } else {
        qc.invalidateQueries({ queryKey: ["sales-summary"] });
        qc.invalidateQueries({ queryKey: ["sales-metrics"] });
        qc.invalidateQueries({ queryKey: ["sales-listings"] });
      }
    },
  });
}

// ---------------------------------------------------------------- sources
export function useSources() {
  return useQuery({
    queryKey: ["sources"],
    queryFn: () => api.get<{ sources: Source[]; credentials: Record<string, boolean> }>("/api/sources"),
  });
}

export function useSourceMutations() {
  const qc = useQueryClient();
  return {
    collect: useMutation({
      mutationFn: ({ sourceId, brandId }: { sourceId: string; brandId: string }) =>
        api.post<any>(`/api/sources/${sourceId}/collect${qs({ brand_id: brandId })}`),
      onSuccess: () => {
        qc.invalidateQueries({ queryKey: ["sources"] });
        qc.invalidateQueries({ queryKey: ["records"] });
        qc.invalidateQueries({ queryKey: ["overview"] });
      },
    }),
  };
}

// ---------------------------------------------------------------- theme
export function useTheme() {
  const [theme, setTheme] = useState<string>(() => localStorage.getItem("theme") || "dark");
  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    localStorage.setItem("theme", theme);
  }, [theme]);
  return { theme, toggle: () => setTheme((t) => (t === "dark" ? "light" : "dark")) };
}

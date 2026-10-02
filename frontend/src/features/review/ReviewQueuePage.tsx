import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { ReviewQueueEntry } from "@/lib/types";

const RISK_STYLES: Record<string, string> = {
  low: "bg-green-100 text-green-800",
  medium: "bg-amber-100 text-amber-800",
  high: "bg-red-100 text-red-800",
};

export function ReviewQueuePage() {
  const { workspaceId = "" } = useParams();
  const [riskFilter, setRiskFilter] = useState("");

  const { data: queue, isLoading } = useQuery({
    queryKey: ["workspace", workspaceId, "review-queue", riskFilter],
    queryFn: () =>
      api.get<ReviewQueueEntry[]>(
        `/workspaces/${workspaceId}/review-queue${riskFilter ? `?risk=${riskFilter}` : ""}`
      ),
    refetchInterval: 5000,
  });

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-slate-900">Review queue</h1>
        <select
          className="h-9 rounded-md border border-slate-300 bg-white px-3 text-sm"
          value={riskFilter}
          onChange={(e) => setRiskFilter(e.target.value)}
        >
          <option value="">All risk levels</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>
      </div>

      {isLoading && <p className="text-slate-500">Loading…</p>}
      {queue?.length === 0 && !isLoading && (
        <p className="text-slate-500">Nothing waiting for review.</p>
      )}

      <div className="flex flex-col gap-3">
        {queue?.map((entry) => (
          <Link key={entry.record_id} to={`/workspaces/${workspaceId}/review/${entry.record_id}`}>
            <Card className="transition-colors hover:border-slate-400">
              <CardHeader className="flex-row items-center justify-between space-y-0">
                <CardTitle className="text-sm">{entry.original_filename}</CardTitle>
                {entry.risk_level && (
                  <span
                    className={`rounded-full px-3 py-1 text-xs font-semibold ${RISK_STYLES[entry.risk_level]}`}
                  >
                    {entry.risk_level.toUpperCase()}
                  </span>
                )}
              </CardHeader>
              {entry.summary && (
                <CardContent className="pt-0 text-sm text-slate-600">{entry.summary}</CardContent>
              )}
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { RecordView } from "@/features/records/RecordView";
import { ApiError, api } from "@/lib/api";
import type { RecordOut, ReviewAction, ReviewOut, TimelineEntry } from "@/lib/types";

export function RecordReviewPage() {
  const { workspaceId = "", recordId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { data: record } = useQuery({
    queryKey: ["record", recordId],
    queryFn: () => api.get<RecordOut>(`/records/${recordId}`),
  });
  const { data: timeline } = useQuery({
    queryKey: ["record", recordId, "audit"],
    queryFn: () => api.get<TimelineEntry[]>(`/records/${recordId}/audit`),
  });

  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);

  const review = useMutation({
    mutationFn: (action: ReviewAction) =>
      api.post<ReviewOut>(`/records/${recordId}/reviews`, { action, comment: comment || undefined }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["workspace", workspaceId, "review-queue"] });
      navigate(`/workspaces/${workspaceId}/review`);
    },
    onError: (err: unknown) => setError(err instanceof ApiError ? err.message : "Could not submit review"),
  });

  if (!record) {
    return <p className="text-slate-500">Loading…</p>;
  }

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <div className="flex flex-col gap-4">
        <h1 className="text-xl font-semibold text-slate-900">Review</h1>
        <iframe
          title="Source document"
          src={`/api/documents/${record.document_id}/file`}
          className="h-[70vh] w-full rounded-md border border-slate-200"
        />
      </div>

      <div className="flex flex-col gap-6">
        <RecordView record={record} />

        <Card>
          <CardHeader>
            <CardTitle>Decision</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <textarea
              className="h-20 w-full rounded-md border border-slate-300 p-2 text-sm"
              placeholder="Comment (required for reject / request correction)"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
            />
            {error && <p className="text-sm text-red-600">{error}</p>}
            <div className="flex gap-2">
              <Button disabled={review.isPending} onClick={() => review.mutate("approve")}>
                Approve
              </Button>
              <Button
                variant="outline"
                disabled={review.isPending}
                onClick={() => review.mutate("request_correction")}
              >
                Request correction
              </Button>
              <Button
                variant="destructive"
                disabled={review.isPending}
                onClick={() => review.mutate("reject")}
              >
                Reject
              </Button>
            </div>
          </CardContent>
        </Card>

        {timeline && timeline.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>Audit timeline</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-2 text-sm">
              {timeline.map((entry, i) => (
                <div key={i} className="border-b border-slate-100 pb-2 last:border-0">
                  <span className="text-xs text-slate-400">
                    {new Date(entry.at).toLocaleString()}
                  </span>{" "}
                  — <strong>{entry.action}</strong>
                  {entry.actor_email && <span className="text-slate-500"> by {entry.actor_email}</span>}
                  {entry.details?.comment != null && (
                    <p className="text-slate-600">&ldquo;{String(entry.details.comment)}&rdquo;</p>
                  )}
                </div>
              ))}
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}

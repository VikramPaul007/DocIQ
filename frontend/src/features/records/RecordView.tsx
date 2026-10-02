import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { RecordOut } from "@/lib/types";

const RISK_STYLES: Record<string, string> = {
  low: "bg-green-100 text-green-800",
  medium: "bg-amber-100 text-amber-800",
  high: "bg-red-100 text-red-800",
};

export function RecordView({ record }: { record: RecordOut }) {
  return (
    <div className="flex flex-col gap-4">
      {record.risk_level && (
        <div className="flex items-center gap-3">
          <span
            className={`rounded-full px-3 py-1 text-sm font-semibold ${RISK_STYLES[record.risk_level]}`}
          >
            {record.risk_level.toUpperCase()} RISK
          </span>
          <span className="text-sm text-slate-600">Score: {record.score ?? "0"}</span>
        </div>
      )}

      {record.summary && (
        <Card>
          <CardHeader>
            <CardTitle>Summary</CardTitle>
          </CardHeader>
          <CardContent className="whitespace-pre-wrap text-sm text-slate-700">
            {record.summary}
          </CardContent>
        </Card>
      )}

      {record.extraction_valid === false && (
        <div className="rounded-md bg-amber-50 p-3 text-sm text-amber-800">
          Extraction did not fully match the schema.
          {record.validation_errors && record.validation_errors.length > 0 && (
            <ul className="mt-1 list-disc pl-5">
              {record.validation_errors.map((e, i) => (
                <li key={i}>{e}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Extracted data</CardTitle>
        </CardHeader>
        <CardContent>
          <pre className="overflow-x-auto rounded bg-slate-50 p-3 text-xs">
            {JSON.stringify(record.extraction, null, 2)}
          </pre>
        </CardContent>
      </Card>

      {record.rule_results && record.rule_results.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Rule results</CardTitle>
          </CardHeader>
          <CardContent>
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-slate-500">
                  <th className="py-1 font-medium">Status</th>
                  <th className="py-1 font-medium">Category</th>
                  <th className="py-1 font-medium">Message</th>
                </tr>
              </thead>
              <tbody>
                {[...record.rule_results]
                  .sort((a, b) => Number(a.passed) - Number(b.passed))
                  .map((r) => (
                    <tr key={r.rule_id} className="border-b border-slate-100">
                      <td className="py-1">
                        <span className={r.passed ? "text-green-700" : "text-red-700"}>
                          {r.passed ? "Pass" : "Fail"}
                        </span>
                      </td>
                      <td className="py-1 capitalize">{r.category}</td>
                      <td className="py-1">{r.message}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}

      {record.contributors && record.contributors.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Risk contributors</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-1 text-sm">
            {record.contributors.map((c) => (
              <div key={c.rule_id}>
                <strong>+{c.weight}</strong> — {c.message}
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {record.ocr && (
        <Card>
          <CardHeader>
            <CardTitle>OCR</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            {record.ocr.pages.map((p) => (
              <div key={p.page} className="text-sm">
                <div className="text-xs text-slate-500">
                  Page {p.page} — {p.method}
                  {p.confidence != null && ` (confidence ${(p.confidence * 100).toFixed(1)}%)`}
                </div>
                <pre className="whitespace-pre-wrap rounded bg-slate-50 p-2 text-xs">{p.text}</pre>
              </div>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

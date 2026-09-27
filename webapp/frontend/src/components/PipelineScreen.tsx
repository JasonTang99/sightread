import type { JobStatus } from "../types";
import { Button } from "./ui";

interface Props {
  job: JobStatus;
  onBack: () => void;
}

export function PipelineScreen({ job, onBack }: Props) {
  return (
    <div className="min-h-screen flex flex-col items-center justify-center gap-4 bg-gray-50">
      <div className="bg-white border border-gray-200 rounded-lg p-8 w-full max-w-lg text-center shadow-sm">
        {job.done && !job.error ? (
          <p className="text-sm font-medium text-green-600">Pipeline complete — loading…</p>
        ) : !job.done ? (
          <>
            <p className="text-sm font-semibold text-gray-700 mb-1">Running pipeline</p>
            <p className="text-xs text-gray-400 mb-4 truncate">{job.folder}</p>
            <div className="w-full bg-gray-100 rounded-full h-1.5 mb-3">
              <div className="bg-blue-500 h-1.5 rounded-full animate-pulse w-1/2" />
            </div>
            {job.last_line && (
              <p className="text-xs text-gray-500 font-mono truncate">{job.last_line}</p>
            )}
          </>
        ) : (
          <>
            <p className="text-sm font-semibold text-red-600 mb-1">Pipeline failed</p>
            <p className="text-xs text-red-600 mb-3">{job.error}</p>
            {(job.lines?.length ?? 0) > 0 && (
              <pre className="text-left text-xs text-gray-600 font-mono bg-gray-50 border border-gray-200 rounded p-2 mb-3 max-h-48 overflow-y-auto whitespace-pre-wrap">
                {job.lines.join("\n")}
              </pre>
            )}
            <Button size="md" onClick={onBack}>
              Back
            </Button>
          </>
        )}
      </div>
    </div>
  );
}

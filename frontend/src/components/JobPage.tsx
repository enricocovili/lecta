import IngestManifest from "./IngestManifest";
import { JobDetail } from "./Jobs";

export default function JobPage({ jobId }: { jobId: number }) {
  return (
    <JobDetail jobId={jobId}>
      {(job) => {
        const active = ["queued", "running"].includes(job.status);
        return <>{job.kind === "ingest" && <IngestManifest jobId={jobId} active={active} />}</>;
      }}
    </JobDetail>
  );
}

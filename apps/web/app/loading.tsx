import { Skeleton } from "@/components/ui";

export default function Loading() {
  return (
    <div className="route-loading">
      <div className="route-loading-sidebar">
        <Skeleton lines={6} />
      </div>
      <div className="route-loading-main">
        <Skeleton lines={8} />
      </div>
    </div>
  );
}

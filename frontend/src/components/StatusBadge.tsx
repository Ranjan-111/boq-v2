import type { ReactNode } from "react";
import type { Badge } from "../lib/statusBadges";

/** A status chip rendered from a pure Badge mapping. */
export default function StatusBadge({ badge, children }: { badge: Badge; children?: ReactNode }) {
  return <span className={badge.className}>{children ?? badge.label}</span>;
}

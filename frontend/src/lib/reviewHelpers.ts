/**
 * Review workspace helpers — pure functions (unit-tested in
 * tests/reviewHelpers.test.ts). No React, no fetch.
 *
 * Doctrine (docs/domain-model.md): the engine's value is immutable and always
 * visible; a human correction lands in corrected_value beside it. The UI must
 * show BOTH — never replace the original with the corrected number.
 */

import type { AuditEntryRow, Measurement } from "./apiClient";

/** The number a row currently bills: the correction when present, else the engine value. */
export function effectiveValue(m: Pick<Measurement, "value" | "corrected_value">): string {
  return m.corrected_value ?? m.value;
}

/** True when the row carries a human correction (drives the "corrected" pill). */
export function isCorrected(m: Pick<Measurement, "corrected_value">): boolean {
  return m.corrected_value !== null && m.corrected_value !== undefined;
}

/**
 * The quantity cell's display parts: original (strikethrough when a
 * correction exists), the corrected number beside it, and the unit.
 */
export interface QuantityDisplay {
  original: string;
  corrected: string | null;
  unit: string;
  correctedPill: boolean;
}

export function quantityDisplay(m: Measurement): QuantityDisplay {
  return {
    original: m.value,
    corrected: m.corrected_value,
    unit: m.unit,
    correctedPill: isCorrected(m),
  };
}

/** Is a review action allowed on this row's state? (server re-checks; UI hints only) */
export function isReviewable(state: string): boolean {
  return state === "measured" || state === "measured_zero" || state === "needs_review";
}

/** Legal element types for the override dropdown (mirrors core.domain.enums). */
export const ELEMENT_TYPES = [
  "wall",
  "room",
  "slab",
  "door",
  "window",
  "opening",
  "floor_finish",
  "other",
] as const;

/** True when the type came from a human override (drives the human_set badge). */
export function isHumanSet(typeSource: string): boolean {
  return typeSource === "human_set";
}

/** Short actor label for the audit list: first 8 chars of the uuid. */
export function actorShort(actor: string): string {
  return actor.slice(0, 8);
}

/** Sort audit rows newest-first (at DESC, id DESC) — the server's order, defended client-side. */
export function sortAuditRows<T extends Pick<AuditEntryRow, "at" | "id">>(rows: T[]): T[] {
  return [...rows].sort((a, b) => {
    const atDelta = Date.parse(b.at) - Date.parse(a.at);
    if (atDelta !== 0) return atDelta;
    return a.id < b.id ? 1 : a.id > b.id ? -1 : 0;
  });
}

/** Keep only rows matching the subject filter ("" = all). */
export function filterBySubjectType<T extends Pick<AuditEntryRow, "subject_type">>(
  rows: T[],
  subjectType: string,
): T[] {
  if (subjectType === "") return rows;
  return rows.filter((r) => r.subject_type === subjectType);
}

/** Distinct subject types present, in first-seen order (drives the filter dropdown). */
export function distinctSubjectTypes<T extends Pick<AuditEntryRow, "subject_type">>(
  rows: T[],
): string[] {
  const seen = new Set<string>();
  for (const r of rows) seen.add(r.subject_type);
  return [...seen];
}

/** One element as it surfaces through its measurements (V1 has no element
 * list endpoint — the measurements list carries the element context). */
export interface ElementGroup {
  element_id: string;
  label: string | null;
  element_type: string;
  type_source: string;
  measurement_count: number;
}

/** Group a run's measurements into distinct elements, first-seen order. */
export function groupElementsByMeasurements(
  items: Pick<Measurement, "element_id" | "element_label" | "element_type" | "type_source">[],
): ElementGroup[] {
  const byId = new Map<string, ElementGroup>();
  for (const m of items) {
    const existing = byId.get(m.element_id);
    if (existing) {
      existing.measurement_count += 1;
      continue;
    }
    byId.set(m.element_id, {
      element_id: m.element_id,
      label: m.element_label,
      element_type: m.element_type,
      type_source: m.type_source,
      measurement_count: 1,
    });
  }
  return [...byId.values()];
}

import { useQuery } from "@tanstack/react-query";
import { api, type DrawingDetail } from "../lib/apiClient";

/**
 * Fetch one drawing's detail (sheets + calibration). Shared by the Drawings
 * tab sheet expander and the Runs tab run form. Returns the DrawingDetail.
 */
export function useSheets(drawingId: string | null): ReturnType<typeof useQuery<DrawingDetail, Error>> {
  return useQuery({
    queryKey: ["drawing", drawingId],
    queryFn: () => api.getDrawing(drawingId!),
    enabled: drawingId !== null,
    staleTime: 15_000,
  });
}

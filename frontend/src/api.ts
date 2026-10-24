export interface StreamInfo {source_id: string; codec: string; width: number; height: number; fps: number | null; frame_count: number | null}
export interface FramePacket {frame_id: string; source_frame_index: number; relative_seconds: number; source_pts: number | null; time_base: string | null; timestamp_basis: string; session_id: string}
export async function get<T>(path: string): Promise<T> {
  const response = await fetch(`/api/v1/${path}`);
  if (!response.ok) throw new Error(`Preview unavailable (${response.status}). Check the local service.`);
  return response.json() as Promise<T>;
}
export function formatTime(seconds: number): string {return `${seconds.toFixed(3)} s`;}

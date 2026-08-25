import type { PbReport } from "@/types/report";

export const API_BASE_URL = "https://ai-pb-service-backend.onrender.com";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

async function parseErrorDetail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") return body.detail;
  } catch {
    // 응답 본문이 JSON이 아닌 경우 상태 텍스트로 대체
  }
  return res.statusText || `요청이 실패했습니다 (HTTP ${res.status})`;
}

/** 저장된 최신 PB 리포트를 조회한다. 아직 리포트가 없으면 404(ApiError)를 던진다. */
export async function fetchLatestReport(): Promise<PbReport> {
  const res = await fetch(`${API_BASE_URL}/api/pb-report`, { cache: "no-store" });
  if (!res.ok) {
    throw new ApiError(res.status, await parseErrorDetail(res));
  }
  return res.json();
}

/** 지정일 기준으로 즉시 수집+리포트 생성을 트리거한다(개발/테스트, 과거 복기용). */
export async function generateReportNow(params: {
  targetDate?: string;
  provider?: string;
}): Promise<PbReport> {
  const res = await fetch(`${API_BASE_URL}/api/generate-now`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      target_date: params.targetDate ?? null,
      provider: params.provider ?? null,
    }),
  });
  if (!res.ok) {
    throw new ApiError(res.status, await parseErrorDetail(res));
  }
  const body = await res.json();
  return body.report as PbReport;
}

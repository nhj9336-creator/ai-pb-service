import type { PbReport } from "@/types/report";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

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

const WAKEUP_RETRY_ATTEMPTS = 3;
const WAKEUP_RETRY_DELAY_MS = 5000;

/**
 * Render 무료 플랜은 일정 시간 요청이 없으면 슬립 상태가 되어, 첫 요청이 서버에
 * 닿기도 전에 연결 자체가 끊겨 fetch()가 TypeError("Failed to fetch")를 던진다.
 * HTTP 응답을 받은 경우(4xx/5xx 포함)는 재시도하지 않고, 순수 네트워크 실패일 때만
 * 서버가 깨어날 시간을 주고 재시도한다.
 */
async function fetchWithWakeupRetry(input: string, init?: RequestInit): Promise<Response> {
  for (let attempt = 1; attempt <= WAKEUP_RETRY_ATTEMPTS; attempt++) {
    try {
      return await fetch(input, init);
    } catch {
      if (attempt < WAKEUP_RETRY_ATTEMPTS) {
        await new Promise((resolve) => setTimeout(resolve, WAKEUP_RETRY_DELAY_MS));
      }
    }
  }
  throw new Error(
    "백엔드 서버에 연결할 수 없습니다. Render 무료 플랜은 일정 시간 뒤 슬립 상태가 되어 " +
      "깨어나는 데 시간이 걸릴 수 있으니, 잠시 후 다시 시도해주세요."
  );
}

/** 저장된 최신 PB 리포트를 조회한다. 아직 리포트가 없으면 404(ApiError)를 던진다. */
export async function fetchLatestReport(): Promise<PbReport> {
  const res = await fetchWithWakeupRetry(`${API_BASE_URL}/api/pb-report`, { cache: "no-store" });
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
  const res = await fetchWithWakeupRetry(`${API_BASE_URL}/api/generate-now`, {
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

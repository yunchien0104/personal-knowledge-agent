import { z } from "zod";
import {
  authenticateApi,
  jsonError,
  jsonSuccess,
  parsePagination,
  validationError,
} from "@/lib/api";

const feedbackSchema = z.object({
  opportunity_id: z.uuid(),
  feedback: z.enum([
    "saved",
    "skip",
    "not_interested",
    "started",
    "completed",
  ]),
}).strict();

export async function GET(request: Request) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;

  const pagination = parsePagination(request.url);
  if ("error" in pagination) return pagination.error;

  try {
    const { data, error } = await auth.supabase
      .from("research_opportunities")
      .select(
        "id,question,rationale,score,status,signal_types,topic_ids,evidence_ids,recommended_at,cooldown_until,created_at"
      )
      .eq("user_id", auth.user.id)
      .order("score", { ascending: false })
      .order("recommended_at", { ascending: false })
      .range(
        pagination.offset,
        pagination.offset + pagination.limit - 1
      );

    if (error) throw error;
    return jsonSuccess(data ?? []);
  } catch {
    return jsonError(
      500,
      "OPPORTUNITIES_LOAD_FAILED",
      "Research suggestions could not be loaded."
    );
  }
}

export async function POST(request: Request) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;

  let body: unknown;

  try {
    body = await request.json();
  } catch {
    return jsonError(
      400,
      "INVALID_JSON",
      "The request body must be valid JSON."
    );
  }

  const parsed = feedbackSchema.safeParse(body);
  if (!parsed.success) return validationError(parsed.error);

  try {
    const { data, error } = await auth.supabase.rpc(
      "record_opportunity_feedback",
      {
        p_opportunity_id: parsed.data.opportunity_id,
        p_feedback: parsed.data.feedback,
      }
    );

    if (error) {
      if (error.code === "P0002") {
        return jsonError(404, "NOT_FOUND", "Suggestion was not found.");
      }
      throw error;
    }

    return jsonSuccess(data);
  } catch {
    return jsonError(
      500,
      "FEEDBACK_FAILED",
      "Suggestion feedback could not be saved."
    );
  }
}

import type { SupabaseClient, User } from "@supabase/supabase-js";
import { z } from "zod";

import { createServerSupabaseClient } from "@/lib/supabase";

const paginationSchema = z.object({
  limit: z.string().regex(/^\d+$/).transform(Number).pipe(z.number().int().min(1).max(100)).optional(),
  offset: z.string().regex(/^\d+$/).transform(Number).pipe(z.number().int().min(0)).optional(),
});

export const uuidSchema = z.uuid();

export const createAssetSchema = z
  .object({
    source_type: z.enum(["pdf", "instagram_reel", "instagram_post", "threads"]),
    source_url: z.url().max(2048).nullable().optional(),
    source_external_id: z.string().trim().min(1).max(512).nullable().optional(),
    source_text: z.string().trim().min(1).max(100000).optional(),
  })
  .strict()
  .superRefine((value, context) => {
    if (value.source_text !== undefined && value.source_type !== "threads") {
      context.addIssue({
        code: "custom",
        path: ["source_text"],
        message: "Fallback text is only supported for Threads assets.",
      });
    }
  });

export type AuthenticatedApi = {
  supabase: SupabaseClient;
  user: User;
};

export function jsonSuccess(data: unknown, status = 200, meta?: object) {
  return Response.json(
    meta ? { data, meta } : { data },
    { status, headers: { "Cache-Control": "no-store" } },
  );
}

export function jsonError(
  status: number,
  code: string,
  message: string,
  details?: unknown,
) {
  return Response.json(
    { data: null, error: { code, message, ...(details ? { details } : {}) } },
    { status, headers: { "Cache-Control": "no-store" } },
  );
}

export async function authenticateApi(): Promise<
  AuthenticatedApi | { response: Response }
> {
  try {
    const supabase = await createServerSupabaseClient();
    const { data, error } = await supabase.auth.getUser();
    if (error || !data.user) {
      return {
        response: jsonError(401, "UNAUTHENTICATED", "Authentication is required."),
      };
    }
    return { supabase, user: data.user };
  } catch {
    return {
      response: jsonError(500, "INTERNAL_ERROR", "The request could not be completed."),
    };
  }
}

export function parsePagination(url: string) {
  const searchParams = new URL(url).searchParams;
  if (searchParams.getAll("limit").length > 1 || searchParams.getAll("offset").length > 1) {
    return { error: jsonError(400, "INVALID_QUERY", "Pagination parameters are invalid.") };
  }

  const parsed = paginationSchema.safeParse({
    limit: searchParams.get("limit") ?? undefined,
    offset: searchParams.get("offset") ?? undefined,
  });
  if (!parsed.success) {
    return { error: jsonError(400, "INVALID_QUERY", "Pagination parameters are invalid.") };
  }
  return { limit: parsed.data.limit ?? 20, offset: parsed.data.offset ?? 0 };
}

export function validationError(error: z.ZodError) {
  return jsonError(
    400,
    "INVALID_REQUEST",
    "The request body is invalid.",
    error.issues.map(({ path, message }) => ({ path: path.join("."), message })),
  );
}

export interface JobPayloadV1 {
  schemaVersion: 1;
  jobId: string;
  userId: string;
  jobType: string;
  idempotencyKey: string;
  input: Record<string, unknown>;
}

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const JOB_TYPE_PATTERN = /^[a-z][a-z0-9_]{1,63}$/;
const ALLOWED_KEYS = new Set([
  "schemaVersion",
  "jobId",
  "userId",
  "jobType",
  "idempotencyKey",
  "input",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireString(
  value: Record<string, unknown>,
  key: string,
): string {
  const candidate = value[key];
  if (typeof candidate !== "string") {
    throw new Error(`${key} must be a string`);
  }
  return candidate;
}

export function parseJobPayloadV1(value: unknown): JobPayloadV1 {
  if (!isRecord(value)) {
    throw new Error("job payload must be an object");
  }
  const unknownKeys = Object.keys(value).filter((key) => !ALLOWED_KEYS.has(key));
  if (unknownKeys.length > 0) {
    throw new Error(`unexpected job payload fields: ${unknownKeys.join(", ")}`);
  }
  if (value.schemaVersion !== 1) {
    throw new Error("schemaVersion must be 1");
  }

  const jobId = requireString(value, "jobId");
  const userId = requireString(value, "userId");
  const jobType = requireString(value, "jobType");
  const idempotencyKey = requireString(value, "idempotencyKey");

  if (!UUID_PATTERN.test(jobId) || !UUID_PATTERN.test(userId)) {
    throw new Error("jobId and userId must be UUIDs");
  }
  if (!JOB_TYPE_PATTERN.test(jobType)) {
    throw new Error("jobType has an invalid format");
  }
  if (idempotencyKey.length < 1 || idempotencyKey.length > 128) {
    throw new Error("idempotencyKey length must be between 1 and 128");
  }
  if (!isRecord(value.input)) {
    throw new Error("input must be an object");
  }

  return {
    schemaVersion: 1,
    jobId,
    userId,
    jobType,
    idempotencyKey,
    input: value.input,
  };
}

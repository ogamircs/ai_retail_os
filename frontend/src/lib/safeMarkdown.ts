import DOMPurify from "dompurify";
import { marked } from "marked";

/**
 * Render markdown to a sanitized HTML string for use with
 * `dangerouslySetInnerHTML`.
 *
 * Wiki bodies + agent-authored artifacts can contain raw HTML (the
 * markdown spec allows it; LLMs sometimes emit `<script>` tags or
 * `javascript:` URLs in citations they hallucinate). Without sanitisation
 * those would execute in the operator's browser. DOMPurify strips the
 * unsafe elements/attrs and returns a string the cockpit can inject
 * directly.
 *
 * Caller is responsible for trusting the body source (this helper does
 * NOT escape the *content*, only neutralises script-execution surfaces).
 */
export function renderSafeMarkdown(body: string | null | undefined): string {
  if (!body) return "";
  const raw = marked.parse(body) as string;
  return DOMPurify.sanitize(raw, {
    USE_PROFILES: { html: true },
    // Don't allow on*=, javascript:, data: URIs in src/href etc.
    FORBID_ATTR: ["onerror", "onload", "onclick", "onmouseover", "onfocus", "onblur"],
    FORBID_TAGS: ["script", "iframe", "object", "embed", "form"],
  });
}

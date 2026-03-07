export interface TaskParams {
    url: string;
    univ_slug: string;
    year: number;
    continue_depth: number;
}

export interface TaskInfo {
    task_id: string;
    state: string;
    progress?: string;
    result?: any;
    error?: string;
    logs?: string[];
    params?: TaskParams;
    tokens_used?: number;
    progress_percent?: number;
    progress_meta?: Record<string, unknown>;
}

export interface StructuredConfig {
    database_url: string;
    llm_priority: string[];
    providers: Record<string, Record<string, string>>;
}

export interface UniversityOption {
    slug: string;
    name: string;
    updated_at: string;
}

export interface LinkCandidate {
    url: string;
    text: string;
}

export interface AnalyzeResult {
    page_type: string;
    links: LinkCandidate[];
    total_found: number;
}

export interface CrawlPayload {
    url: string;
    univ_slug: string;
    year: number;
    continue_depth: number;
    page_type_hint: string;
    export_md?: boolean;
    export_path?: string;
    html_content?: string;
    selected_urls?: string[];
    selected_link_texts?: Record<string, string>;
    browser_automation_enabled?: boolean;
    detail_pages_batch?: DetailPageBatchItem[];
    batch_index?: number;
    batch_total?: number;
    taxonomy_enabled: boolean;
    taxonomy_low_threshold: number;
    taxonomy_high_threshold: number;
    taxonomy_hint_top_k: number;
    taxonomy_override_enabled: boolean;
}

export interface DetailPageBatchItem {
    url: string;
    html_content: string;
    selected_anchor_text?: string;
}

export interface ProgramRecord {
    id: number | null;
    name_en: string;
    name_zh: string | null;
    academic_year: number;
    faculty: string | null;
    program_group_code: string | null;
    tuition_amount: number | null;
    currency: string | null;
    study_options: { mode: string; duration_months: number }[];
    deadlines: { round?: number; description?: string; cutoff_date?: string }[];
    requirements?: {
        category?: string;
        subject_name?: string;
        framework?: string;
        exam_name?: string;
        minimum_value?: string;
        unit?: string;
        applicant_scope?: string;
        requirement_text?: string;
        evidence_url?: string;
        sort_order?: number;
    }[];
    requirement_version?: {
        version_no?: number;
        effective_at?: string;
        valid_from?: string;
        valid_to?: string | null;
        change_summary?: string | null;
    } | null;
    source_url: string | null;
}

export type ProgramPatchPayload = Partial<
    Pick<
        ProgramRecord,
        | "name_en"
        | "name_zh"
        | "faculty"
        | "program_group_code"
        | "tuition_amount"
        | "currency"
        | "study_options"
        | "deadlines"
        | "requirements"
        | "source_url"
    >
>;

export type StatusType = "success" | "error" | "info";
export type ShowStatusFn = (msg: string, type: StatusType) => void;

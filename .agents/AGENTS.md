# Custom rules for ArturoCarrilloJimenez/WebScrapingDistributed

- **No commits or pushes**: Do NOT commit or push changes automatically. Present the proposed changes (diffs/code) to the user first, explain them, and wait for explicit approval before making any commits or pushes, unless the user explicitly tells you to do so.

- **Connected Memory (Obsidian - AI Brain)**:
  - **Initialization:** At the start of any session or when tackling a new task, you must proactively read:
    1. The developer profile (`AI_Brain/01_Fixed_Context/engineer_profile.md`)
    2. The projects index MOC (`AI_Brain/02_Projects/projects_index.md`)
    3. The project overview (`AI_Brain/02_Projects/WebScrapingDistributed/overview.md`)
    4. The backlog board (`AI_Brain/02_Projects/WebScrapingDistributed/backlog.md`)
    5. The latest session log in `AI_Brain/03_Telemetry_Logs/session_logs/`.
  - **Wiki Lint Pass:** During initialization, you must run a "Wiki Lint" pass to identify and report:
    1. Mismatches between documented files/directories and actual code on the active git branch.
    2. Broken links or invalid markdown connections in the Obsidian vault.
    3. Stale or inconsistent status details between the code state and the backlog/overview.
  - **Raw Sources Ingestion:** Check the raw sources inbox (`AI_Brain/05_Raw_Sources/`). If new text resources, papers, or articles exist, parse their contents, generate summaries, and integrate their knowledge into the relevant concept or entity files in the wiki.
  - **Standards & Contracts:** Consult architectural guidelines (`AI_Brain/01_Fixed_Context/architectural_standards.md`), dependency maps (`AI_Brain/02_Projects/WebScrapingDistributed/connection_map.md`), and data contracts (`AI_Brain/02_Projects/WebScrapingDistributed/data_contracts.md`) during implementation.
  - **Session Persistence & Log Formatting:** Track all changes. When the user requests to wrap up or close the session, compile the updates:
    1. Create/update the session log (naming format: `AI_Brain/03_Telemetry_Logs/session_logs/YYYY-MM-DD_session_X.md`).
    2. The session log header MUST match the parseable format: `# Session Log: YYYY-MM-DD — Session X | <status>`.
    3. Update the backlog board (`AI_Brain/02_Projects/WebScrapingDistributed/backlog.md`).
    4. Append the milestone to the engineering diary (`AI_Brain/03_Telemetry_Logs/engineering_diary.md`).

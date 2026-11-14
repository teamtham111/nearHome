# NearHome Qwen Discovery Variant: Agent Workflow

> **Status:** Product-design proposal only. This describes a Qwen-enabled
> discovery flow; it is not part of the current deployed NearHome product.

## As-is versus to-be

```mermaid
flowchart LR
    subgraph ASIS[As-is: buyer and human property-agent workflow]
        A1[Buyer explains needs<br/>by call, chat or meeting] --> A2[Human agent interprets the brief<br/>and asks follow-up questions]
        A2 --> A3[Human agent manually searches<br/>property portals, contacts and own network]
        A3 --> A4[Human agent reads listings, checks rough fit<br/>and keeps notes across chats/spreadsheets]
        A4 --> A5[Human agent sends a selection<br/>of listings to buyer]
        A5 --> A6[Buyer saves, rejects or asks questions]
        A6 --> A7[Human agent manually revises search]
        A7 --> A3
        A6 --> A8[Buyer eventually selects homes<br/>for viewing and comparison]
    end

    subgraph TOBE[To-be: Qwen multi-agent discovery workflow + NearHome]
        B1[Buyer describes their brief] --> B2[Qwen Conversation Orchestrator]
        B2 --> B3[Qwen Brief Agent<br/>creates a structured BuyerBrief]
        B3 --> B4[Buyer confirms hard requirements,<br/>preferences and search boundary]
        B4 --> B5[Qwen Search-Plan Agent<br/>creates site queries and search plan]
        B5 --> B6[Qwen Source Agents<br/>one connector per approved property site]
        B6 --> B7[Qwen Listing-Extraction Agent<br/>turns source pages into listing records]
        B7 --> B8[Data Quality and Deduplication Service]
        B8 --> B9[Deterministic Hard-Filter Service]
        B9 --> B10[Qwen Match and Shortlist Agent]
        B10 --> B11[Qwen Explanation Agent<br/>creates candidate cards and trade-offs]
        B11 --> B12[Buyer saves, dismisses or refines]
        B12 -->|Feedback or refined brief| B2
        B12 -->|2–5 saved homes| B13[NearHome validation, enrichment<br/>and deterministic comparison]
    end
```

## To-be: detailed multi-agent process

```mermaid
flowchart TD
    U[Buyer brief<br/>budget, flat type, areas, commute, schools, trade-offs] --> O[Qwen Conversation Orchestrator]

    subgraph QWEN[Qwen agents]
        O --> BA[1. Brief Agent]
        BA --> BP[Draft BuyerBrief:<br/>hard requirements, soft preferences,<br/>important locations, unknowns]
        BP --> C{Buyer confirms<br/>the brief?}
        C -- Needs clarification --> BA
        C -- Yes --> SPA[2. Search-Plan Agent]
        SPA --> PLAN[Site-specific query plan:<br/>filters, priority signals, crawl scope]

        PLAN --> SA[3. Source Agents]
        SA --> S1[Approved Site A connector]
        SA --> S2[Approved Site B connector]
        SA --> SN[Other approved-site connectors]

        S1 --> LEA[4. Listing-Extraction Agent]
        S2 --> LEA
        SN --> LEA
        LEA --> RAW[Normalised candidate fields plus<br/>source URL, retrieval time, raw snapshot]

        RAW --> MSA[5. Match and Shortlist Agent]
        MSA --> REASONS[Candidate-level match reasons,<br/>trade-offs and low-supply explanation]
        REASONS --> EA[6. Explanation Agent]
        EA --> CARDS[Buyer-facing candidate cards]
    end

    RAW --> DQ[Deterministic data-quality service:<br/>validate, normalise, deduplicate,<br/>mark stale/missing/conflicting data]
    DQ --> HF[Deterministic hard-filter service:<br/>enforce confirmed budget, flat type,<br/>location and other supported must-haves]
    HF --> MSA

    CARDS --> ACTION{Buyer action}
    ACTION -- Save --> SHORTLIST[Working shortlist]
    ACTION -- Dismiss with reason --> FEEDBACK[Session preference feedback]
    ACTION -- Refine constraints --> O
    FEEDBACK --> SPA

    SHORTLIST --> READY{2–5 homes<br/>ready?}
    READY -- No --> SPA
    READY -- Yes --> NH[NearHome: buyer confirms fields,<br/>evidence enrichment, deterministic comparison]
    NH --> RESULT[Explainable recommendation<br/>and trade-offs]
```

## What each Qwen agent does

| Qwen agent | Responsibility | Input | Output | Must not do |
| --- | --- | --- | --- | --- |
| Conversation Orchestrator | Own the conversation state and route work to the right specialist agent. | Buyer messages, confirmed brief, search status and feedback. | Next question, status update, or specialist-agent task. | Change confirmed requirements or present a listing as verified. |
| Brief Agent | Translate natural language into a buyer-readable structured brief; surface ambiguity. | Buyer needs and answers. | Draft `BuyerBrief`: hard requirements, preferences, locations, unknowns and assumptions. | Guess a budget, relaxation or requirement without buyer confirmation. |
| Search-Plan Agent | Turn the confirmed brief into a transparent, site-specific retrieval plan. | Confirmed `BuyerBrief`, site capabilities and session feedback. | Queries, crawl schedule, fields required and ranking signals. | Directly rank homes or circumvent a site's allowed access method. |
| Source Agent (one per approved site) | Retrieve currently available listings from its assigned property site. | Site query and connector configuration. | Raw listing snapshots, source URLs, retrieval times, status and errors. | Bypass logins, paywalls, robots rules, rate limits or terms of use. |
| Listing-Extraction Agent | Extract comparable listing fields and preserve evidence from each raw source result. | Raw listing snapshot and permitted metadata. | Candidate listing records with quoted evidence, missing fields and extraction confidence. | Invent missing fields or treat marketing claims as verified facts. |
| Match and Shortlist Agent | Produce a diverse set of eligible candidates and a clear low-supply explanation. | Hard-filtered listing records, confirmed preferences and feedback. | Candidate ordering, match reasons, trade-offs and optional relaxation suggestions. | Override a hard filter, secretly personalise by protected attributes, or pick the final home. |
| Explanation Agent | Write concise, evidence-linked candidate cards and answer buyer questions. | Candidate order, raw facts, source dates and limitations. | Plain-language reasons, cautions and comparison prompts. | Add unsupported claims, hide missing data or describe a ranking as certain. |

## Deterministic services that sit beside the Qwen agents

These are deliberately not delegated to Qwen:

- **Data-quality and deduplication service:** canonicalises address, price,
  floor area and flat type; detects the same unit across sites; records
  conflicts, source metadata and stale/unavailable status.
- **Hard-filter service:** enforces buyer-confirmed non-negotiables such as
  budget, flat type, required area and supported minimum size/lease rules.
- **NearHome comparison pipeline:** validates fields with buyer review,
  enriches saved listings with transaction, lease, transport, school and
  journey evidence, then performs deterministic requirement checks, scoring
  and recommendation explanation.

## Source-access rule

“All approved property websites” means every site for which NearHome has an
explicitly approved connector. A connector must use the site's permitted API,
partner feed or compliant crawler, respect rate limits and terms, and retain
source provenance. Failed or inaccessible sites must be reported to the buyer;
the agents must never imply exhaustive coverage when a connector did not run.

## Buyer-visible candidate card

Every shortlisted search result should display:

- asking price, flat type, floor area, address/town, source site and retrieval
  time;
- matched hard requirements and any missing, stale or unverified field;
- two or three brief-specific fit reasons and one material trade-off;
- a link to the source listing; and
- **Save**, **Dismiss**, **Compare later** and **Refine brief** actions.

Only when the buyer saves 2–5 homes does the flow enter existing NearHome.
NearHome then remains the trusted comparison layer, rather than letting a
language model make an opaque property-buying decision.

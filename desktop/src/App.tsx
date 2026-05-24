/**
 * Component gallery (§31.2 / §31.4). The temporary entry while the design
 * system lands — every theme.py component rendered once so the look can be
 * screenshot-diffed against the Streamlit views (§31.7). Replaced by the real
 * router + views in Phase 11.4+.
 */
import type { ReactNode } from "react";
import {
  Callout,
  CandidateCard,
  CitationChip,
  ConfidenceBadge,
  ConsoleLogRow,
  CostMeter,
  Dim,
  Hairline,
  IwhMeter,
  Kicker,
  Numeric,
  ReadoutCard,
  RecommendedActionBadge,
  ScoreBank,
  SpecimenBlock,
  StatusChip,
  TokenTtlCountdown,
} from "./components";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section style={{ marginBottom: "2rem" }}>
      <Kicker>{title}</Kicker>
      <div style={{ marginTop: "0.6rem" }}>{children}</div>
    </section>
  );
}

export function App() {
  return (
    <main style={{ maxWidth: 900, margin: "0 auto", padding: "2.5rem 2rem 5rem" }}>
      <Kicker>X Growth Dashboard · native shell</Kicker>
      <h1>Instrument Panel</h1>
      <p className="dim" style={{ maxWidth: 620 }}>
        Phase 11.2 design-system port — every <code>theme.py</code> component recreated 1:1 as a
        React component over the shared token layer. Charts (Plotly.js) and the wired views land
        in 11.4+.
      </p>

      <Hairline />

      <Section title="Readout cards">
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "0.6rem" }}>
          <ReadoutCard label="Followers" value="64" caption="+3 from baseline" />
          <ReadoutCard label="Last backup" value="5m ago" accent="phosphorDim" />
          <ReadoutCard label="Next milestone" value="—" caption="no data yet" empty />
        </div>
      </Section>

      <Section title="Score bank · §29.3 R / E / S / O">
        <ScoreBank relevance={3} engagementSurface={2} saturation={1} replyOpportunity={3} />
        <ScoreBank
          relevance={2}
          engagementSurface={1}
          saturation={null}
          replyOpportunity={null}
          engagementFootnote="floor — no author size"
        />
      </Section>

      <Section title="IWH meter · cost meter · token TTL">
        <IwhMeter intelligence={3} wisdom={2} humility={3} />
        <div style={{ maxWidth: 320, marginTop: "1rem" }}>
          <CostMeter mtdUsd={18.4} capUsd={25} />
        </div>
        <div style={{ marginTop: "1rem" }}>
          <CostMeter mtdUsd={24.9} capUsd={25} />
        </div>
        <div style={{ marginTop: "1.4rem" }}>
          <TokenTtlCountdown secondsRemaining={42} />
        </div>
      </Section>

      <Section title="Confidence (4-tier) · recommended action (no red) · status">
        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", marginBottom: "0.6rem" }}>
          <ConfidenceBadge tier="insufficient" label="insufficient" />
          <ConfidenceBadge tier="directional" label="directional" />
          <ConfidenceBadge tier="tentative" label="tentative" />
          <ConfidenceBadge tier="confident" label="confident" />
        </div>
        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", marginBottom: "0.6rem" }}>
          <RecommendedActionBadge label="reply_now" />
          <RecommendedActionBadge label="reply_if_time" />
          <RecommendedActionBadge label="consider" />
          <RecommendedActionBadge label="skip" />
          <RecommendedActionBadge label={null} />
        </div>
        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
          <StatusChip label="unprocessed" tone="neutral" />
          <StatusChip label="processing" tone="active" />
          <StatusChip label="over cap" tone="warn" />
          <StatusChip label="processed" tone="done" />
          <StatusChip label="failed" tone="failed" />
        </div>
      </Section>

      <Section title="Callout · citations">
        <Callout>
          Daily follower movement is noisy. <em>Don't overreact to ±1.</em>
        </Callout>
        <div>
          Grounded in <CitationChip recordType="post" idOrFilter="#412" /> and{" "}
          <CitationChip recordType="lane" idOrFilter="stir×icp×ask" stripped />.
        </div>
      </Section>

      <Section title="Specimen block (immutable)">
        <SpecimenBlock text={"Raw brain-dump text Daniel pasted.\nPreserved verbatim — not editable."} />
      </Section>

      <Section title="Candidate draft card">
        <CandidateCard
          index={1}
          text={"What if dinner decided itself? Scan your kitchen, get 3 cookable options in 60s."}
          pillar="stir"
          audience="icp"
          cta="ask"
          contentType="value"
          rationale="Leads with the working-parent pain, ends on a concrete proof point."
        >
          <div style={{ marginTop: "0.5rem", display: "flex", gap: "0.5rem" }}>
            <button className="primary" type="button">
              send to drafts
            </button>
            <button type="button">discard</button>
          </div>
        </CandidateCard>
      </Section>

      <Section title="Console log rows (Agent Chat sessions)">
        <ConsoleLogRow timestamp="09:41" kind="session" title="Draft three openings about Stir" active />
        <ConsoleLogRow timestamp="08:12" kind="session" title="Why did this post underperform?" />
      </Section>

      <Section title="Inline numerics & dim text">
        <p>
          Shipped <Numeric>12</Numeric> replies across <Numeric>3</Numeric> lanes.{" "}
          <Dim>Sample size still below the discrimination floor.</Dim>
        </p>
      </Section>
    </main>
  );
}

"use client";

import { useState } from "react";
import type { Methodology } from "@/lib/methodology";
import { MethodologyDiagram } from "@/components/methodology-diagrams";

export function MethodologyExplorer({ item }: { item: Methodology }) {
  const [activeId, setActiveId] = useState(item.components[0].id);
  const active = item.components.find((component) => component.id === activeId) ?? item.components[0];
  const weighted = item.components.every((component) => component.pct !== undefined);
  const baseId = `${item.id}-explorer`;

  return (
    <div className="nh-method-explorer">
      <p className="nh-method-label">{item.selectorTitle.toUpperCase()}</p>
      <div className={`nh-method-weights${weighted ? "" : " nh-method-steps"}`} role="tablist" aria-label={item.selectorTitle}>
        {item.components.map((component, index) => (
          <button
            key={component.id}
            type="button"
            role="tab"
            id={`${baseId}-tab-${component.id}`}
            aria-selected={component.id === active.id}
            aria-controls={`${baseId}-panel`}
            className={`nh-method-seg nh-method-seg-${index % 4}${component.id === active.id ? " is-active" : ""}`}
            style={weighted ? { flexGrow: component.pct } : { flexGrow: 1 }}
            onClick={() => setActiveId(component.id)}
          >
            <span>{component.label}</span>
            {component.pct !== undefined && <b>{component.pct}%</b>}
          </button>
        ))}
      </div>
      <p className="nh-method-hint">Select a part to see how it is scored and what its terms mean.</p>

      <div className="nh-method-panel" role="tabpanel" id={`${baseId}-panel`} aria-labelledby={`${baseId}-tab-${active.id}`} key={active.id}>
        <h3>
          {active.label}
          {active.pct !== undefined && <span> · {active.pct}% of the {item.label.toLowerCase()} score</span>}
        </h3>
        <p className="nh-method-measures">{active.measures}</p>
        <p className="nh-method-why"><b>Why:</b> {active.why}</p>

        {active.diagrams && (
          <div className="nh-method-diagrams">
            {active.diagramTitle && <h4 className="nh-method-blocktitle">{active.diagramTitle}</h4>}
            {active.diagrams.map((diagram) => <MethodologyDiagram key={diagram} id={diagram} />)}
          </div>
        )}

        <div className="nh-method-grid">
          {(active.groups.length > 0 || active.facts) && (
            <div className="nh-method-titled">
              {active.groupsTitle && <h4 className="nh-method-blocktitle">{active.groupsTitle}</h4>}
            <div className="nh-method-card">
              {active.groups.length === 0 && <h4>What to know</h4>}
              {active.groups.map((group) => (
                <div key={group.title} className="nh-method-group">
                  <h4>{group.title}</h4>
                  <ul className="nh-method-bands">
                    {group.bands.map((band) => (
                      <li key={band.label}>
                        <span>{band.label}</span>
                        <span className="nh-method-bar"><i style={{ width: `${Math.max(band.value > 0 ? 5 : 0, (band.value / group.barMax) * 100)}%` }} /></span>
                        <b>{band.text ?? `${band.value}${group.unit}`}</b>
                      </li>
                    ))}
                  </ul>
                  {group.note && <p className="nh-method-note">{group.note}</p>}
                </div>
              ))}
              {active.facts && <ul className={`nh-method-facts${active.groups.length === 0 ? " nh-method-facts-first" : ""}`}>{active.facts.map((fact) => <li key={fact}>{fact}</li>)}</ul>}
            </div>
            </div>
          )}
          <div className={`nh-method-card${active.groupsTitle ? " nh-method-card-offset" : ""}`}>
            <h4>Terms we use</h4>
            <dl className="nh-method-terms">
              {active.terms.map((entry) => (
                <div key={entry.term}><dt>{entry.term}</dt><dd>{entry.meaning}</dd></div>
              ))}
            </dl>
          </div>
        </div>

        {active.example && (
          <details className="nh-method-more nh-method-example-details">
            <summary>See a worked example <span aria-hidden="true">＋</span></summary>
            <div className="nh-method-example">
              <h4>{active.example.title}</h4>
              <ol>{active.example.steps.map((step) => <li key={step}>{step}</li>)}</ol>
              <p className="nh-method-result">{active.example.result}</p>
            </div>
          </details>
        )}
      </div>
    </div>
  );
}

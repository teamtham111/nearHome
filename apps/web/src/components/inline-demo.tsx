"use client";

import { startTransition, useEffect, useState } from "react";

export function InlineDemo() {
  const [showDemo, setShowDemo] = useState(false);
  const [demoClosed, setDemoClosed] = useState(false);
  const [demoRevision, setDemoRevision] = useState(0);

  useEffect(() => {
    // The walkthrough itself runs in this frame. Avoid recursively embedding
    // another copy of the homepage inside its demo homepage step.
    if (new URLSearchParams(window.location.search).get("demoEmbed") !== "1") {
      startTransition(() => setShowDemo(true));
    }
    const closeEmbeddedDemo = (event: MessageEvent) => {
      if (event.origin !== window.location.origin || event.data?.type !== "nearhome-demo-exit") return;
      setShowDemo(false);
      setDemoClosed(true);
    };
    window.addEventListener("message", closeEmbeddedDemo);
    return () => window.removeEventListener("message", closeEmbeddedDemo);
  }, []);

  const restartDemo = () => {
    setDemoClosed(false);
    setDemoRevision((revision) => revision + 1);
    setShowDemo(true);
  };

  return (
    <section className="nh-landing-shell nh-landing-demo" id="how-it-works-demo" aria-labelledby="inline-demo-heading">
      <div className="nh-landing-demo-heading">
        <div>
          <p className="nh-landing-eyebrow nh-landing-eyebrow-left">HOW IT WORKS</p>
          <h2 id="inline-demo-heading">See how it works, step by step.</h2>
          <p>Follow a shortlist from your first preferences to a side-by-side comparison.</p>
        </div>
      </div>
      <div className="nh-landing-demo-frame-wrap">
        {showDemo ? (
          <iframe
            key={demoRevision}
            className="nh-landing-demo-frame"
            src="/?demo=1&demoStep=0&demoEmbed=1"
            title="Interactive NearHome walkthrough"
            loading="lazy"
            sandbox="allow-scripts allow-same-origin allow-forms"
            allow="clipboard-read; clipboard-write"
          />
        ) : demoClosed ? (
          <div className="nh-landing-demo-closed"><p>Walkthrough closed. Your place on the homepage is still here.</p><button type="button" onClick={restartDemo}>Restart walkthrough</button></div>
        ) : (
          <div className="nh-landing-demo-loading" aria-hidden="true" />
        )}
      </div>
    </section>
  );
}

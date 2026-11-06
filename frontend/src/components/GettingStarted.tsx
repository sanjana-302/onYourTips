import { useState } from "react";
import type { AppStatus } from "../api/types";
import { hrefFor } from "../lib/router";
import { getFlag, setFlag } from "../lib/storage";
import { Icon } from "./Icon";
import { RefreshButton } from "./RefreshButton";

/** Three-step guide shown until every step is done or it's dismissed. */
export function GettingStarted({ status }: { status: AppStatus | undefined }) {
  const [dismissed, setDismissed] = useState(() => getFlag("guide-dismissed"));
  if (!status || dismissed) return null;

  const steps = [
    { done: status.articles > 0, title: "Collect your first stories", hint: "Fetch the latest from 20 sources. Takes about a minute." },
    { done: status.saved > 0, title: "Save a story you like", hint: "Tap Save on any story. Saves teach the app what to show you." },
    { done: getFlag("visited-week"), title: "Check your week", hint: "The Week tab collects the best stories of the last 7 days." },
  ];
  const current = steps.findIndex((s) => !s.done);
  if (current === -1) return null;
  const step = steps[current];

  const close = () => {
    setFlag("guide-dismissed");
    setDismissed(true);
  };

  return (
    <section className="guide card beige" aria-label="Getting started">
      <div className="guide-top">
        <ol className="steps" aria-label={`Step ${current + 1} of 3`}>
          {steps.map((s, i) => (
            <li key={s.title} data-done={s.done || undefined} data-current={i === current || undefined}>
              <span className="step-dot">
                {s.done ? <Icon name="check" size={13} strokeWidth={3} /> : i + 1}
              </span>
            </li>
          ))}
        </ol>
        <button className="icon-btn small" aria-label="Hide this guide" onClick={close}>
          <Icon name="close" size={16} />
        </button>
      </div>
      <div className="guide-body">
        <div>
          <p className="guide-kicker">Getting started</p>
          <h2>{step.title}</h2>
          <p className="guide-hint">{step.hint}</p>
        </div>
        {current === 0 ? <RefreshButton variant="cta" label="Fetch stories" /> : null}
        {current === 2 ? (
          <a className="btn dark small" href={hrefFor("week")}>
            Open Week
          </a>
        ) : null}
      </div>
    </section>
  );
}

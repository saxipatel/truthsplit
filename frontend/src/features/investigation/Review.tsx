import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../../api/client";
import type { Investigation, InvestigationStatus } from "../../api/types";
import { Button, Card, ErrorMessage, EvidenceBadge, Field, inputClass, Loading, StatusBadge } from "../../components/ui";
import { formatDateTime, humanize } from "../../lib/format";
import { useAction, useApi } from "../../lib/useApi";

const NEXT: Record<InvestigationStatus, InvestigationStatus[]> = {
  open: ["in_review", "resolved", "archived"],
  in_review: ["open", "resolved", "archived"],
  resolved: ["open", "archived"],
  archived: ["open"],
};

function describeEvent(action: string, meta: Record<string, unknown>): string {
  switch (action) {
    case "investigation.created":
      return `Investigation created (delta ${String(meta.delta ?? "")})${meta.backfilled ? " · backfilled record" : ""}`;
    case "investigation.status_changed":
      return `Status ${humanize(String(meta.from_status))} → ${humanize(String(meta.to_status))}`;
    case "note.added":
      return `${humanize(String(meta.decision))} note${meta.finding_rule ? ` on ${String(meta.finding_rule)}` : ""}`;
    case "simulation.run":
      return `Simulation: ${humanize(String(meta.rule_type))} → delta ${String(meta.after_delta ?? "")}`;
    default:
      return action;
  }
}

export default function Review({ inv, onChanged }: { inv: Investigation; onChanged: () => void }) {
  const notes = useApi(() => api.notes(inv.id), [inv.id]);
  const history = useApi(() => api.history(inv.id), [inv.id]);
  const similar = useApi(() => api.similar(inv.id), [inv.id]);
  const [status, setStatus] = useState<InvestigationStatus>(NEXT[inv.status][0]);
  const [statusNote, setStatusNote] = useState("");
  const [reviewer, setReviewer] = useState("");
  const [decision, setDecision] = useState<"comment" | "accepted" | "rejected">("comment");
  const [findingId, setFindingId] = useState("");
  const [note, setNote] = useState("");
  const [noteError, setNoteError] = useState<string>();
  const changeStatus = useAction(api.setStatus);
  const addNote = useAction(api.addNote);

  const refresh = () => {
    notes.reload();
    history.reload();
    onChanged();
  };

  const submitStatus = async () => {
    const done = await changeStatus.run(inv.id, { status, note: statusNote || undefined, reviewer: reviewer || undefined });
    if (done) {
      setStatusNote("");
      refresh();
    }
  };
  const submitNote = async () => {
    if (!note.trim()) return setNoteError("Write a note.");
    if (decision !== "comment" && !findingId) return setNoteError("Choose the finding you are accepting or rejecting.");
    setNoteError(undefined);
    const done = await addNote.run(inv.id, { decision, note, finding_id: findingId || null, reviewer: reviewer || undefined });
    if (done) {
      setNote("");
      refresh();
    }
  };
  const findingTitle = (id: string | null) => inv.findings.find((f) => f.id === id)?.title;

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <div className="space-y-4">
        <Card title="Status">
          <p className="mb-3 text-sm">
            Current status: <StatusBadge status={inv.status} />
          </p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Move to" htmlFor="rv-status">
              <select id="rv-status" className={inputClass} value={status} onChange={(e) => setStatus(e.target.value as InvestigationStatus)}>
                {NEXT[inv.status].map((s) => (
                  <option key={s} value={s}>
                    {humanize(s)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Your name (optional)" htmlFor="rv-reviewer">
              <input id="rv-reviewer" className={inputClass} value={reviewer} maxLength={120} onChange={(e) => setReviewer(e.target.value)} />
            </Field>
          </div>
          <div className="mt-3">
            <Field label={status === "resolved" ? "Resolution note (required)" : "Note (optional)"} htmlFor="rv-status-note">
              <textarea id="rv-status-note" rows={2} className={inputClass} value={statusNote} onChange={(e) => setStatusNote(e.target.value)} />
            </Field>
          </div>
          <Button className="mt-3" variant="primary" pending={changeStatus.pending} onClick={submitStatus}>
            Change status
          </Button>
          <div className="mt-2">
            <ErrorMessage error={changeStatus.error} title="Status not changed" />
          </div>
        </Card>

        <Card title="Add a review note">
          <p className="mb-3 text-xs text-slate-500">Accepting or rejecting records your decision. It does not change a finding's evidence status, which reflects only what the data proves.</p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Decision" htmlFor="rv-decision">
              <select id="rv-decision" className={inputClass} value={decision} onChange={(e) => setDecision(e.target.value as typeof decision)}>
                <option value="comment">Comment</option>
                <option value="accepted">Accept a finding</option>
                <option value="rejected">Reject a finding</option>
              </select>
            </Field>
            <Field label={decision === "comment" ? "Finding (optional)" : "Finding"} htmlFor="rv-finding">
              <select id="rv-finding" className={inputClass} value={findingId} onChange={(e) => setFindingId(e.target.value)}>
                <option value="">— none —</option>
                {inv.findings.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.title}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <div className="mt-3">
            <Field label="Note" htmlFor="rv-note">
              <textarea id="rv-note" rows={3} className={inputClass} value={note} maxLength={5000} onChange={(e) => setNote(e.target.value)} />
            </Field>
          </div>
          <Button className="mt-3" variant="primary" pending={addNote.pending} onClick={submitNote}>
            Save note
          </Button>
          <div className="mt-2 space-y-2">
            <ErrorMessage error={noteError} title="Check the note" />
            <ErrorMessage error={addNote.error} title="Note not saved" />
          </div>
        </Card>

        <Card title="Notes">
          {notes.error && <ErrorMessage error={notes.error} />}
          {!notes.data && !notes.error && <Loading />}
          {notes.data?.length === 0 && <p className="text-sm text-slate-500">No notes yet.</p>}
          <ul className="space-y-3">
            {notes.data?.map((n) => (
              <li key={n.id} className="text-sm">
                <p className="text-xs text-slate-500">
                  {formatDateTime(n.reviewed_at)} · {n.reviewer ?? "unnamed"} · <span className="font-medium text-slate-700">{humanize(n.decision)}</span>
                  {n.finding_id && <> · {findingTitle(n.finding_id)}</>}
                </p>
                <p className="whitespace-pre-wrap">{n.note}</p>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <div className="space-y-4">
        <Card title="Similar investigations">
          {similar.error && <ErrorMessage error={similar.error} />}
          {!similar.data && !similar.error && <Loading />}
          {similar.data && (
            <>
              <p className="mb-3 text-xs text-slate-500">{similar.data.method}</p>
              {similar.data.matches.length === 0 && <p className="text-sm text-slate-500">No other investigation shares rules, definitions or sources with this one.</p>}
              <ul className="space-y-3">
                {similar.data.matches.map((m) => (
                  <li key={m.investigation_id} className="rounded border border-slate-200 p-3 text-sm">
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <Link to={`/investigations/${m.investigation_id}`} className="font-medium text-blue-800 hover:underline">
                        {m.title}
                      </Link>
                      <span className="text-xs text-slate-600">score {m.score}</span>
                    </div>
                    <ul className="mt-1 list-inside list-disc text-xs text-slate-600">
                      {m.reasons.map((r) => (
                        <li key={r}>{r}</li>
                      ))}
                    </ul>
                    {m.accepted_diagnoses.length > 0 && (
                      <div className="mt-2 rounded bg-slate-50 p-2 text-xs">
                        <p className="font-medium text-slate-700">Accepted there (suggestion only, not applied here):</p>
                        {m.accepted_diagnoses.map((d) => (
                          <p key={d.reviewed_at + d.rule_id}>
                            {d.finding_title} — “{d.note}”{d.applies_here ? " · this investigation has a finding from the same rule" : ""}
                          </p>
                        ))}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
        </Card>

        <Card title="History">
          {history.error && <ErrorMessage error={history.error} />}
          {!history.data && !history.error && <Loading />}
          <ol className="space-y-2">
            {history.data?.map((e, i) => (
              <li key={i} className="text-sm">
                <p className="text-xs text-slate-500">
                  {formatDateTime(e.occurred_at)} · {e.actor}
                </p>
                <p>{describeEvent(e.action, e.metadata_json)}</p>
              </li>
            ))}
          </ol>
        </Card>

        <Card title="Findings at a glance">
          <ul className="space-y-1 text-sm">
            {inv.findings.map((f) => (
              <li key={f.id} className="flex flex-wrap items-center gap-2">
                <EvidenceBadge status={f.evidence_status} /> {f.title}
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  );
}

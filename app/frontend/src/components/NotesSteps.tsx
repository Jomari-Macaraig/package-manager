import type { BoxNotes } from '../api/typesV2'

type Props = {
  notes: BoxNotes[]
  cutNotes: string[]
  /** box type names, index 0 = physical box 1 */
  boxNames?: string[]
}

/** Loading steps per box, then the cut-down heights. */
export function NotesSteps({ notes, cutNotes, boxNames = [] }: Props) {
  return (
    <div className="space-y-3">
      <h3 className="text-sm font-medium">Loading steps</h3>
      {notes.length === 0 ? (
        <p className="text-sm text-slate-600">No loading steps.</p>
      ) : (
        notes.map((n) => (
          <div key={n.box}>
            <h4 className="text-sm font-medium">
              Box {n.box}
              {boxNames[n.box - 1] ? ` — ${boxNames[n.box - 1]}` : ''}
            </h4>
            <ol className="list-decimal space-y-0.5 pl-5 text-sm">
              {n.steps.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ol>
          </div>
        ))
      )}
      <div>
        <h3 className="text-sm font-medium">Cut-down heights</h3>
        {cutNotes.length === 0 ? (
          <p className="text-sm text-slate-600">No cuts needed.</p>
        ) : (
          <ul className="list-disc pl-5 text-sm">
            {cutNotes.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

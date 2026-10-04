type Props = {
  value: string
  onChange: (v: string) => void
  max: number
  disabled?: boolean
}

export function InstructionsBox({ value, onChange, max, disabled }: Props) {
  const over = value.length > max
  return (
    <section className="space-y-1">
      <label htmlFor="instructions" className="block text-base font-semibold">
        Packing instructions
      </label>
      <textarea
        id="instructions"
        className="h-24 w-full rounded border border-slate-300 p-2 text-sm aria-[invalid=true]:border-red-500"
        aria-invalid={over}
        aria-describedby="instructions-count"
        value={value}
        disabled={disabled}
        placeholder="e.g. keep mugs upright, 0.125 in padding"
        onChange={(e) => onChange(e.target.value)}
      />
      <p
        id="instructions-count"
        className={'text-xs ' + (over ? 'text-red-700' : 'text-slate-500')}
      >
        {value.length} / {max} characters
      </p>
    </section>
  )
}

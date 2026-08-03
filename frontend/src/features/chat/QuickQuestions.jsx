export default function QuickQuestions({ questions, onPick, disabled }) {
  if (!questions?.length) return null

  return (
    <div className="quick-questions">
      {questions.map((q) => (
        <button
          key={q.label}
          type="button"
          className="quick-chip"
          disabled={disabled}
          onClick={() => onPick(q.question)}
        >
          {q.label}
        </button>
      ))}
    </div>
  )
}

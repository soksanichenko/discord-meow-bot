interface ScoresProps {
  scores: Record<string, number>;
  playerNames: Record<string, string>;
  answerer?: string | null;
}

export function Scores({ scores, playerNames, answerer }: ScoresProps) {
  const sorted = Object.entries(scores).sort(([, a], [, b]) => b - a);
  return (
    <div style={{ display: 'flex', gap: '1rem', justifyContent: 'center', flexWrap: 'wrap' }}>
      {sorted.map(([id, score]) => (
        <div
          key={id}
          style={{
            background: answerer === id ? '#1565c0' : '#1a2035',
            border: `1px solid ${answerer === id ? '#42a5f5' : '#2a3a5a'}`,
            borderRadius: 8,
            padding: '0.4rem 1rem',
            minWidth: 100,
            textAlign: 'center',
          }}
        >
          <div style={{ fontSize: '0.75rem', color: '#90a4ae' }}>{playerNames[id] ?? id}</div>
          <div style={{ fontWeight: 700, color: score < 0 ? '#ef5350' : '#ffd54f' }}>
            {score}
          </div>
        </div>
      ))}
    </div>
  );
}

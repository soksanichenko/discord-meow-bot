import { useState } from 'react';

interface ScoreCorrectionProps {
  scores: Record<string, number>;
  playerNames: Record<string, string>;
  onApply: (adjustments: Record<string, number>) => void;
  onClose: () => void;
}

export function ScoreCorrection({ scores, playerNames, onApply, onClose }: ScoreCorrectionProps) {
  const [deltas, setDeltas] = useState<Record<string, string>>(
    () => Object.fromEntries(Object.keys(scores).map(id => [id, '0']))
  );

  const setDelta = (id: string, value: string) => {
    setDeltas(prev => ({ ...prev, [id]: value }));
  };

  const handleApply = () => {
    const adjustments: Record<string, number> = {};
    for (const [id, raw] of Object.entries(deltas)) {
      const n = parseInt(raw, 10);
      if (!isNaN(n) && n !== 0) adjustments[id] = n;
    }
    onApply(adjustments);
    onClose();
  };

  return (
    <div style={{
      position: 'absolute', inset: 0, zIndex: 30,
      background: 'rgba(0,0,0,0.82)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}>
      <div style={{
        background: '#1a2035', border: '1px solid #1976d2', borderRadius: 10,
        padding: '1.5rem', minWidth: 300, display: 'flex', flexDirection: 'column', gap: '1rem',
      }}>
        <h3 style={{ margin: 0, color: '#90caf9' }}>Score Correction</h3>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
          {Object.entries(scores).map(([id, score]) => {
            const delta = parseInt(deltas[id] ?? '0', 10) || 0;
            const result = score + delta;
            return (
              <div key={id} style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                <span style={{ flex: 1, color: '#fff', fontSize: '0.95rem' }}>
                  {playerNames[id] ?? id}
                </span>
                <span style={{ color: '#90caf9', width: 50, textAlign: 'right', fontSize: '0.9rem' }}>
                  {score}
                </span>
                <input
                  type="number"
                  value={deltas[id] ?? '0'}
                  onChange={e => setDelta(id, e.target.value)}
                  style={{
                    width: 70, padding: '0.3rem', fontSize: '0.9rem',
                    background: '#0d1b2e', color: '#fff', border: '1px solid #1976d2', borderRadius: 4,
                    textAlign: 'center',
                  }}
                />
                <span style={{ color: delta === 0 ? '#90caf9' : delta > 0 ? '#81c784' : '#ef9a9a', width: 54, textAlign: 'right', fontSize: '0.9rem' }}>
                  → {result}
                </span>
              </div>
            );
          })}
        </div>

        <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'flex-end', marginTop: '0.5rem' }}>
          <button onClick={onClose} style={{ background: 'transparent', color: '#90caf9', border: '1px solid #1976d2', borderRadius: 6, padding: '0.5rem 1.2rem' }}>
            Cancel
          </button>
          <button onClick={handleApply} style={{ background: '#1565c0', color: '#fff', borderRadius: 6, padding: '0.5rem 1.2rem' }}>
            Apply
          </button>
        </div>
      </div>
    </div>
  );
}

import type { GameState } from '../types';
import { Scores } from './Scores';

interface BoardProps {
  state: GameState;
  playerId: string;
  send: (op: string, data?: Record<string, unknown>) => void;
}

export function Board({ state, playerId, send }: BoardProps) {
  const isActive = state.active_player_id === playerId;
  const cols = state.board.length;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '1rem', gap: '1rem' }}>
      <header style={{ textAlign: 'center' }}>
        <h1 style={{ fontSize: '1.2rem', color: '#90caf9' }}>{state.round_name}</h1>
        {isActive && (
          <p style={{ fontSize: '0.8rem', color: '#81c784', marginTop: 4 }}>Your turn — pick a question</p>
        )}
      </header>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: `repeat(${cols}, 1fr)`,
          gap: '0.4rem',
          flex: 1,
          overflow: 'auto',
        }}
      >
        {state.board.map((theme, tIdx) => (
          <div key={tIdx} style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
            <div
              style={{
                background: '#0d47a1',
                borderRadius: 6,
                padding: '0.5rem 0.25rem',
                textAlign: 'center',
                fontSize: '0.75rem',
                fontWeight: 600,
                color: '#bbdefb',
                minHeight: 48,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              {theme.name}
            </div>
            {theme.questions.map((q, qIdx) => (
              <button
                key={qIdx}
                disabled={q.played || !isActive}
                onClick={() => send('select', { theme_idx: tIdx, question_idx: qIdx })}
                style={{
                  background: q.played ? '#1a2035' : '#1565c0',
                  color: q.played ? 'transparent' : '#ffd54f',
                  fontWeight: 700,
                  fontSize: '1.1rem',
                  padding: '0.8rem 0',
                  borderRadius: 6,
                  border: `1px solid ${q.played ? '#2a3a5a' : '#1976d2'}`,
                }}
              >
                {q.played ? '' : q.price}
              </button>
            ))}
          </div>
        ))}
      </div>

      <Scores scores={state.scores} playerNames={state.player_names} />
    </div>
  );
}

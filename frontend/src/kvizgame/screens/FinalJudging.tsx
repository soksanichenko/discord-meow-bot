import type { GameState } from '../types';
import { Scores } from './Scores';

interface FinalJudgingProps {
  state: GameState;
  isHost: boolean;
  send: (op: string, data?: Record<string, unknown>) => void;
}

export function FinalJudging({ state, isHost, send }: FinalJudgingProps) {
  const { scores, player_names, final_current_judge_id, final_current_answer, final_current_bid } = state;
  const judgeName = final_current_judge_id ? (player_names[final_current_judge_id] ?? final_current_judge_id) : null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '1.5rem', gap: '1.5rem', alignItems: 'center', justifyContent: 'center' }}>
      <h2 style={{ color: '#ffd54f', margin: 0 }}>Final Judging</h2>

      {judgeName && (
        <div style={{ textAlign: 'center', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          <p style={{ color: '#90caf9', fontSize: '1.1rem', margin: 0 }}>
            <strong>{judgeName}</strong>
          </p>
          <div style={{ background: '#1a2035', border: '1px solid #1976d2', borderRadius: 8, padding: '1rem 1.5rem', minWidth: 280 }}>
            <p style={{ color: '#aaa', fontSize: '0.8rem', margin: '0 0 0.4rem' }}>Answer:</p>
            <p style={{ color: '#fff', fontSize: '1.2rem', margin: 0 }}>
              {final_current_answer || <em style={{ color: '#666' }}>no answer</em>}
            </p>
          </div>
          {isHost && (
            <p style={{ color: '#aaa', fontSize: '0.85rem', margin: 0 }}>
              Bid: <strong style={{ color: '#ffd54f' }}>{final_current_bid ?? 0}</strong>
            </p>
          )}
        </div>
      )}

      {isHost && final_current_judge_id && (
        <div style={{ display: 'flex', gap: '1rem' }}>
          <button
            onClick={() => send('judge_final', { correct: true })}
            style={{ background: '#2e7d32', color: '#fff', fontSize: '1.1rem', padding: '0.6rem 1.8rem', borderRadius: 6 }}
          >
            ✓ Correct
          </button>
          <button
            onClick={() => send('judge_final', { correct: false })}
            style={{ background: '#c62828', color: '#fff', fontSize: '1.1rem', padding: '0.6rem 1.8rem', borderRadius: 6 }}
          >
            ✗ Wrong
          </button>
        </div>
      )}

      {!isHost && (
        <p style={{ color: '#aaa' }}>Host is judging answers…</p>
      )}

      <Scores scores={scores} playerNames={player_names} />
    </div>
  );
}

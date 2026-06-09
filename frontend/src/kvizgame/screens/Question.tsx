import type { GameState } from '../types';
import { Scores } from './Scores';

interface QuestionProps {
  state: GameState;
  playerId: string;
  send: (op: string, data?: Record<string, unknown>) => void;
}

export function Question({ state, playerId, send }: QuestionProps) {
  const { phase, current_question: cq, active_player_id, current_answerer_id, scores, player_names } = state;
  const isActive = active_player_id === playerId;
  const isAnswerer = current_answerer_id === playerId;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '1rem', gap: '1rem' }}>
      {cq && (
        <div style={{ textAlign: 'center', color: '#90caf9', fontSize: '0.85rem' }}>
          {cq.theme_name} · <strong style={{ color: '#ffd54f' }}>{cq.price}</strong>
        </div>
      )}

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '1rem' }}>
        {cq?.scenario.map((atom, i) => {
          if (atom.type === 'text' || atom.type === 'say') {
            return <p key={i} style={{ fontSize: '1.4rem', textAlign: 'center', maxWidth: 600 }}>{atom.content}</p>;
          }
          if (atom.type === 'image' || atom.type === 'audio' || atom.type === 'voice' || atom.type === 'video') {
            // Media served from backend — TODO: implement /api/media endpoint
            return <p key={i} style={{ color: '#90a4ae', fontSize: '0.9rem' }}>[{atom.type}: {atom.content}]</p>;
          }
          return null;
        })}
      </div>

      {phase === 'ANSWER_RESULT' && cq && (
        <div style={{ textAlign: 'center', color: '#a5d6a7', fontSize: '1rem' }}>
          Answer: <strong>{cq.right.join(' / ')}</strong>
        </div>
      )}

      <Controls
        phase={phase}
        isActive={isActive}
        isAnswerer={isAnswerer}
        answerer={current_answerer_id}
        send={send}
      />

      <Scores scores={scores} playerNames={player_names} answerer={current_answerer_id} />
    </div>
  );
}

interface ControlsProps {
  phase: GameState['phase'];
  isActive: boolean;
  isAnswerer: boolean;
  answerer: string | null;
  send: (op: string, data?: Record<string, unknown>) => void;
}

function Controls({ phase, isActive, isAnswerer, answerer, send }: ControlsProps) {
  if (phase === 'QUESTION' && isActive) {
    return (
      <div style={{ textAlign: 'center' }}>
        <button onClick={() => send('open_buzzer')} style={{ background: '#1565c0', color: '#fff', fontSize: '1rem', padding: '0.6rem 2rem' }}>
          Open Buzzer
        </button>
      </div>
    );
  }

  if (phase === 'BUZZER_OPEN') {
    return (
      <div style={{ textAlign: 'center' }}>
        <button
          onClick={() => send('buzz')}
          style={{ background: '#b71c1c', color: '#fff', fontSize: '1.6rem', fontWeight: 700, padding: '1rem 3rem', borderRadius: 12 }}
        >
          BUZZ!
        </button>
      </div>
    );
  }

  if (phase === 'ANSWERING') {
    return (
      <div style={{ textAlign: 'center', color: '#ffd54f' }}>
        {isAnswerer ? 'Your turn to answer!' : `${player_names[answerer!] ?? answerer} is answering…`}
        {isActive && (
          <div style={{ display: 'flex', gap: '1rem', justifyContent: 'center', marginTop: '0.8rem' }}>
            <button onClick={() => send('judge', { correct: true })} style={{ background: '#2e7d32', color: '#fff', fontSize: '1rem', padding: '0.6rem 1.5rem' }}>✓ Correct</button>
            <button onClick={() => send('judge', { correct: false })} style={{ background: '#c62828', color: '#fff', fontSize: '1rem', padding: '0.6rem 1.5rem' }}>✗ Wrong</button>
          </div>
        )}
      </div>
    );
  }

  if (phase === 'ANSWER_RESULT' && isActive) {
    return (
      <div style={{ textAlign: 'center' }}>
        <button onClick={() => send('advance')} style={{ background: '#1565c0', color: '#fff', padding: '0.6rem 2rem' }}>
          Continue →
        </button>
      </div>
    );
  }

  return null;
}

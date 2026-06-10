import { useState } from 'react';
import type { GameState } from '../types';
import { Scores } from './Scores';

const MEDIA_FOLDER: Record<string, string> = {
  image: 'Images', voice: 'Audio', audio: 'Audio', video: 'Video',
};

interface FinalQuestionProps {
  state: GameState;
  channelId: string;
  playerId: string;
  isHost: boolean;
  send: (op: string, data?: Record<string, unknown>) => void;
}

export function FinalQuestion({ state, channelId, playerId, isHost, send }: FinalQuestionProps) {
  const { scores, player_names, final_theme_name, final_question, final_answers_submitted = [] } = state;
  const [answer, setAnswer] = useState('');
  const hasAnswered = final_answers_submitted.includes(playerId);
  const total = Object.keys(scores).length;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '1.5rem', gap: '1rem' }}>
      <div style={{ textAlign: 'center', color: '#90caf9', fontSize: '0.85rem' }}>
        Final · <strong style={{ color: '#ffd54f' }}>{final_theme_name}</strong>
      </div>

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '1rem' }}>
        {final_question?.scenario.map((atom, i) => {
          if (atom.type === 'text' || atom.type === 'say') {
            return <p key={i} style={{ fontSize: '1.4rem', textAlign: 'center', maxWidth: 600 }}>{atom.content}</p>;
          }
          const folder = MEDIA_FOLDER[atom.type];
          if (folder) {
            const filename = atom.content.replace(/^@/, '');
            const url = `/api/media/${channelId}/${folder}/${encodeURIComponent(filename)}`;
            if (atom.type === 'image') return <img key={i} src={url} style={{ maxWidth: '100%', maxHeight: 300, objectFit: 'contain' }} />;
            if (atom.type === 'audio' || atom.type === 'voice') return <audio key={i} autoPlay controls src={url} style={{ width: '100%', maxWidth: 500 }} />;
            if (atom.type === 'video') return <video key={i} autoPlay controls src={url} style={{ maxWidth: '100%', maxHeight: 300 }} />;
          }
          return null;
        })}
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.5rem' }}>
        {!isHost && (
          hasAnswered ? (
            <p style={{ color: '#81c784' }}>✓ Answer submitted</p>
          ) : (
            <div style={{ display: 'flex', gap: '0.5rem', width: '100%', maxWidth: 500 }}>
              <input
                type="text"
                placeholder="Your answer…"
                value={answer}
                onChange={e => setAnswer(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && answer.trim() && send('submit_final_answer', { answer })}
                style={{ flex: 1, padding: '0.5rem', fontSize: '1rem', background: '#1a2035', color: '#fff', border: '1px solid #1976d2', borderRadius: 4 }}
              />
              <button
                onClick={() => send('submit_final_answer', { answer })}
                disabled={!answer.trim()}
                style={{ background: '#1565c0', color: '#fff', padding: '0.5rem 1.2rem', borderRadius: 4 }}
              >
                Submit
              </button>
            </div>
          )
        )}

        <p style={{ color: '#aaa', fontSize: '0.85rem' }}>
          {final_answers_submitted.length}/{total} answers submitted
        </p>

        {isHost && (
          <button
            onClick={() => send('start_final_judging')}
            style={{ background: '#6a1b9a', color: '#fff', fontSize: '1rem', padding: '0.6rem 2rem', borderRadius: 6 }}
          >
            Start Judging →
          </button>
        )}
      </div>

      <Scores scores={scores} playerNames={player_names} />
    </div>
  );
}

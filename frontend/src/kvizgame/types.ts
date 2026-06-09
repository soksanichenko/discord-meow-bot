export type Phase =
  | 'BOARD'
  | 'AUCTION_BIDDING'
  | 'CAT_TRANSFER'
  | 'QUESTION'
  | 'BUZZER_OPEN'
  | 'ANSWERING'
  | 'ANSWER_RESULT'
  | 'ROUND_END'
  | 'GAME_OVER';

export interface Atom {
  type: string;
  content: string;
  time: number;
}

export interface CurrentQuestion {
  theme_name: string;
  price: number;
  q_type: string;
  scenario: Atom[];
  right: string[];
}

export interface BoardQuestion {
  price: number;
  played: boolean;
}

export interface BoardTheme {
  name: string;
  questions: BoardQuestion[];
}

export interface GameState {
  phase: Phase;
  player_names: Record<string, string>;
  active_player_id: string | null;
  scores: Record<string, number>;
  round_name: string | null;
  board: BoardTheme[];
  current_question: CurrentQuestion | null;
  current_answerer_id: string | null;
  connected_players: string[];
}

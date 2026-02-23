import tkinter as tk
from board import Board

class ShogiGame:
    def __init__(self):
        self.board = Board()
        self.current_player = 1 # 1: 先手, -1: 後手
        self.selected_piece_coords = None # 選択中の駒の座標 (row, col)
        self.legal_moves_for_selected_piece = [] # 選択中の駒の合法手リスト

        self.on_update_callback = None # GUI更新のためのコールバック

    def set_update_callback(self, callback):
        self.on_update_callback = callback

    def get_board_state(self):
        return self.board.grid

    def get_current_player(self):
        return self.current_player

    def get_selected_piece_coords(self):
        return self.selected_piece_coords

    def get_legal_moves_for_selected_piece(self):
        return self.legal_moves_for_selected_piece

    def handle_click(self, clicked_row, clicked_col):
        if self.selected_piece_coords:
            # 駒が選択されている場合、移動先としてクリックされたと判断
            from_row, from_col = self.selected_piece_coords
            
            # 合法手判定を行い、移動を試みる
            if self.try_move_piece(from_row, from_col, clicked_row, clicked_col):
                self.switch_player_turn()
            
            self.selected_piece_coords = None # 選択解除
            self.legal_moves_for_selected_piece = [] # 合法手ハイライトをクリア
        else:
            # 駒が選択されていない場合、クリックされた位置の駒を選択
            piece = self.board.get_piece(clicked_row, clicked_col)
            if piece and piece.player == self.current_player: # 現在のプレイヤーの駒のみ選択可能
                self.selected_piece_coords = (clicked_row, clicked_col)
                self.legal_moves_for_selected_piece = self.board.get_legal_moves(clicked_row, clicked_col) # 合法手を取得
            else:
                self.selected_piece_coords = None
                self.legal_moves_for_selected_piece = []
        
        if self.on_update_callback:
            self.on_update_callback() # GUIに更新を通知
    
    def try_move_piece(self, from_row, from_col, to_row, to_col):
        if self.board.move_piece(from_row, from_col, to_row, to_col):
            print(f"Moved piece from ({from_row}, {from_col}) to ({to_row}, {to_col})")
            return True
        else:
            print("Invalid move.")
            return False

    def switch_player_turn(self):
        self.current_player *= -1 # プレイヤーを交代


class ShogiGUI:
    def __init__(self, master):
        self.master = master
        master.title("将棋")

        self.game = ShogiGame()
        self.game.set_update_callback(self.draw_board) # ゲーム状態更新時にdraw_boardを呼ぶ

        # UIフレーム
        self.info_frame = tk.Frame(master)
        self.info_frame.pack(side=tk.TOP, fill=tk.X)

        self.turn_label = tk.Label(self.info_frame, text="", font=("Arial", 16))
        self.turn_label.pack(side=tk.LEFT, padx=10, pady=5)

        self.canvas = tk.Canvas(master, width=500, height=500, bg="light gray")
        self.canvas.pack()

        self.draw_board() # 初回描画
        self.canvas.bind("<Button-1>", self.on_canvas_click)

    def get_player_name(self, player):
        return "先手" if player == 1 else "後手"

    def draw_board(self):
        self.canvas.delete("all") # Clear canvas

        cell_size = 500 / 9 # 9x9 grid
        
        # Draw grid lines
        for i in range(10):
            self.canvas.create_line(0, i * cell_size, 500, i * cell_size, fill="black")
            self.canvas.create_line(i * cell_size, 0, i * cell_size, 500, fill="black")

        # Draw pieces
        board_grid = self.game.get_board_state()
        for r in range(9):
            for c in range(9):
                piece = board_grid[r][c]
                if piece:
                    x_center = c * cell_size + cell_size / 2
                    y_center = r * cell_size + cell_size / 2
                    
                    fill_color = "red" if piece.player == -1 else "blue"
                    self.canvas.create_text(x_center, y_center, 
                                            text=str(piece), 
                                            font=("Arial", 16, "bold"), 
                                            fill=fill_color)
        
        # Highlight selected piece
        selected_coords = self.game.get_selected_piece_coords()
        if selected_coords:
            r, c = selected_coords
            self.canvas.create_rectangle(c * cell_size, r * cell_size, 
                                         (c + 1) * cell_size, (r + 1) * cell_size, 
                                         outline="yellow", width=3)
        
        # Highlight legal moves
        legal_moves = self.game.get_legal_moves_for_selected_piece()
        for r, c in legal_moves:
            self.canvas.create_rectangle(c * cell_size, r * cell_size, 
                                         (c + 1) * cell_size, (r + 1) * cell_size, 
                                         outline="green", width=3)
        
        # Update turn label
        self.turn_label.config(text=self.get_player_name(self.game.get_current_player()) + " の番")


    def on_canvas_click(self, event):
        cell_size = 500 / 9
        clicked_col = int(event.x / cell_size)
        clicked_row = int(event.y / cell_size)
        
        self.game.handle_click(clicked_row, clicked_col)


def main():
    root = tk.Tk()
    game_gui = ShogiGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()
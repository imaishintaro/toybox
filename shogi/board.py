# Shogi Board Logic

from piece import Piece, Pawn, Lance, Knight, Silver, Gold, Bishop, Rook, King, PromotedPawn

class Board:
    def __init__(self):
        self.grid = [[None for _ in range(9)] for _ in range(9)]
        self.initialize_board()

    def initialize_board(self):
        # 駒の初期配置を定義するヘルパー関数
        def place_pieces(player, row_offset):
            # 1段目 (playerの視点)
            self.grid[row_offset][0] = Lance(player)
            self.grid[row_offset][1] = Knight(player)
            self.grid[row_offset][2] = Silver(player)
            self.grid[row_offset][3] = Gold(player)
            self.grid[row_offset][4] = King(player)
            self.grid[row_offset][5] = Gold(player)
            self.grid[row_offset][6] = Silver(player)
            self.grid[row_offset][7] = Knight(player)
            self.grid[row_offset][8] = Lance(player)

            # 2段目
            self.grid[row_offset + (1 * player)][1] = Rook(player)
            self.grid[row_offset + (1 * player)][7] = Bishop(player)

            # 3段目
            for i in range(9):
                self.grid[row_offset + (2 * player)][i] = Pawn(player)

        # 先手 (Player 1) の駒を配置
        # 先手は0行目から配置を開始し、行番号が増える方向(下)に駒を配置
        place_pieces(1, 0) 

        # 後手 (Player -1) の駒を配置
        # 後手は8行目から配置を開始し、行番号が減る方向(上)に駒を配置
        place_pieces(-1, 8)



    def get_piece(self, row, col):
        if 0 <= row < 9 and 0 <= col < 9:
            return self.grid[row][col]
        return None

    def get_legal_moves(self, r, c):
        """
        指定された位置 (r, c) の駒の合法手を返す。
        """
        piece = self.get_piece(r, c)
        if piece:
            return piece.get_possible_moves(r, c, self.grid)
        return []

    def move_piece(self, from_row, from_col, to_row, to_col):
        piece = self.get_piece(from_row, from_col)
        if piece:
            # 合法手リストに含まれているかチェック
            legal_moves = self.get_legal_moves(from_row, from_col)
            if (to_row, to_col) in legal_moves:
                self.grid[to_row][to_col] = piece
                self.grid[from_row][from_col] = None
                return True
        return False
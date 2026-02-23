# Shogi Piece Logic

class Piece:
    def __init__(self, name, player):
        self.name = name # 例: '歩', '香', '桂', '銀', '金', '角', '飛', '玉'
        self.player = player # 1 (先手) or -1 (後手)
        self.promoted = False # 成っているか

    def __str__(self): # このメソッドは変更なし
        prefix = ""
        if self.player == 1: # 先手
            prefix = "" # 先手は通常表示
        elif self.player == -1: # 後手
            prefix = "v" # 後手は 'v' をつけて表示 (例: v歩)
        
        display_name = self.name
        if self.promoted:
            display_name = "成" + display_name # 成り駒は '成' をつけて表示

        return prefix + display_name

    def get_possible_moves(self, r, c, board_grid):
        """
        指定された駒の現在の位置 (r, c) から移動可能なマスのリストを返す。
        board_grid は 9x9 の盤面を表す。
        """
        moves = []
        # 各駒の具体的な動きはサブクラスで実装
        return moves

class Pawn(Piece):
    def __init__(self, player):
        super().__init__("歩", player)

    def get_possible_moves(self, r, c, board_grid):
        moves = []
        # 歩の動き: プレイヤーの方向に1マス前進
        # 先手 (player=1) は行番号が減る方向 (上)
        # 後手 (player=-1) は行番号が増える方向 (下)
        
        next_r = r - self.player # 先手なら r-1, 後手なら r+1

        if 0 <= next_r < 9:
            target_piece = board_grid[next_r][c]
            if target_piece is None or target_piece.player != self.player:
                # ターゲットマスが空か、相手の駒であれば移動可能
                moves.append((next_r, c))
        return moves

class Lance(Piece):
    def __init__(self, player):
        super().__init__("香", player)

class Knight(Piece):
    def __init__(self, player):
        super().__init__("桂", player)

class Silver(Piece):
    def __init__(self, player):
        super().__init__("銀", player)

class Gold(Piece):
    def __init__(self, player):
        super().__init__("金", player)

class Bishop(Piece):
    def __init__(self, player):
        super().__init__("角", player)

class Rook(Piece):
    def __init__(self, player):
        super().__init__("飛", player)

class King(Piece):
    def __init__(self, player):
        super().__init__("玉", player)

# 成り駒 (例: と金)
class PromotedPawn(Pawn):
    def __init__(self, player):
        super().__init__(player)
        self.name = "と"
        self.promoted = True

# 他の成り駒も同様に定義できます
# PromotedLance, PromotedKnight, PromotedSilver, PromotedBishop, PromotedRook
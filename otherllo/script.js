// オセロゲームのJavaScriptロジック

const boardElement = document.getElementById('board');
const currentTurnElement = document.getElementById('current-turn');
const blackCountElement = document.getElementById('black-count');
const whiteCountElement = document.getElementById('white-count');
const messageElement = document.getElementById('message');

const BOARD_SIZE = 8;
const EMPTY = 0;
const BLACK = 1;
const WHITE = 2;

let board = [];
let currentPlayer = BLACK;

function initializeBoard() {
    board = Array(BOARD_SIZE).fill(null).map(() => Array(BOARD_SIZE).fill(EMPTY));
    
    // 初期配置
    board[3][3] = WHITE;
    board[3][4] = BLACK;
    board[4][3] = BLACK;
    board[4][4] = WHITE;
}

function drawBoard() {
    boardElement.innerHTML = '';
    let blackCount = 0;
    let whiteCount = 0;

    for (let r = 0; r < BOARD_SIZE; r++) {
        for (let c = 0; c < BOARD_SIZE; c++) {
            const cell = document.createElement('div');
            cell.classList.add('cell');
            cell.dataset.row = r;
            cell.dataset.col = c;
            cell.addEventListener('click', handleCellClick);

            if (board[r][c] === BLACK) {
                const disk = document.createElement('div');
                disk.classList.add('disk', 'black');
                cell.appendChild(disk);
                blackCount++;
            } else if (board[r][c] === WHITE) {
                const disk = document.createElement('div');
                disk.classList.add('disk', 'white');
                cell.appendChild(disk);
                whiteCount++;
            }
            boardElement.appendChild(cell);
        }
    }

    currentTurnElement.textContent = currentPlayer === BLACK ? '黒' : '白';
    blackCountElement.textContent = blackCount;
    whiteCountElement.textContent = whiteCount;
    messageElement.textContent = '';
}

function handleCellClick(event) {
    const row = parseInt(event.target.dataset.row);
    const col = parseInt(event.target.dataset.col);

    if (isValidMove(row, col)) {
        placeDiskAndFlip(row, col);
        switchPlayer();
        drawBoard();
        // ゲーム終了判定など
    } else {
        messageElement.textContent = 'そこには置けません！';
    }
}

function isValidMove(row, col) {
    // 既に駒がある場合は置けない
    if (board[row][col] !== EMPTY) {
        return false;
    }

    // ひっくり返せる駒があるかチェック
    return getFlippableDisks(row, col).length > 0;
}

function getFlippableDisks(row, col) {
    const flippable = [];
    const opponent = currentPlayer === BLACK ? WHITE : BLACK;

    // 8方向をチェック
    for (let dr = -1; dr <= 1; dr++) {
        for (let dc = -1; dc <= 1; dc++) {
            if (dr === 0 && dc === 0) continue; // 中央はスキップ

            let r = row + dr;
            let c = col + dc;
            const lineToFlip = [];

            while (r >= 0 && r < BOARD_SIZE && c >= 0 && c < BOARD_SIZE && board[r][c] === opponent) {
                lineToFlip.push({r, c});
                r += dr;
                c += dc;
            }

            // 自分の駒で挟めているか
            if (r >= 0 && r < BOARD_SIZE && c >= 0 && c < BOARD_SIZE && board[r][c] === currentPlayer) {
                flippable.push(...lineToFlip);
            }
        }
    }
    return flippable;
}

function placeDiskAndFlip(row, col) {
    board[row][col] = currentPlayer;
    const flippable = getFlippableDisks(row, col);
    flippable.forEach(pos => {
        board[pos.r][pos.c] = currentPlayer;
    });
}

function switchPlayer() {
    currentPlayer = currentPlayer === BLACK ? WHITE : BLACK;
}

// ゲーム開始
initializeBoard();
drawBoard();

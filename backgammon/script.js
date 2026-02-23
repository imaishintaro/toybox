// バックギャモンゲームのJavaScriptロジック

const boardElement = document.getElementById('board');
const currentPlayerElement = document.getElementById('current-player');
const rollDiceButton = document.getElementById('roll-dice-button');
const die1Element = document.getElementById('die1');
const die2Element = document.getElementById('die2');
const messageElement = document.getElementById('message');

const RED = 1; // プレイヤー1
const WHITE = 2; // プレイヤー2

// 盤面データ構造
// points[0]からpoints[23]まで、バックギャモンの24のポイントを表す
// 各ポイントは { player: RED/WHITE, count: 駒の数 } を持つ
// points[24]はRedのベアオフ、points[25]はWhiteのベアオフ
// points[26]はRedのバー、points[27]はWhiteのバー
let points = Array(28).fill(null).map(() => ({ player: 0, count: 0 }));
let currentPlayer = RED;
let dice = [0, 0];

function initializeGame() {
    // 駒の初期配置
    // ポイント1 (インデックス0) - Whiteのスタート
    points[0] = { player: WHITE, count: 2 };
    // ポイント6 (インデックス5) - Redのホームボード
    points[5] = { player: RED, count: 5 };
    // ポイント8 (インデックス7) - Redのホームボード
    points[7] = { player: RED, count: 3 };
    // ポイント12 (インデックス11) - Redのアウターボード
    points[11] = { player: RED, count: 5 };
    
    // ポイント13 (インデックス12) - Whiteのアウターボード
    points[12] = { player: WHITE, count: 5 };
    // ポイント17 (インデックス16) - Whiteのホームボード
    points[16] = { player: WHITE, count: 3 };
    // ポイント19 (インデックス18) - Whiteのホームボード
    points[18] = { player: WHITE, count: 5 };
    // ポイント24 (インデックス23) - Redのスタート
    points[23] = { player: RED, count: 2 };

    currentPlayer = RED;
    updateUI();
    drawBoard();
}

function drawBoard() {
    boardElement.innerHTML = '';
    
    // 上段のポイント (13-24) - 盤面の上半分
    const topRow = document.createElement('div');
    topRow.classList.add('board-row', 'top-row');
    // 盤面上のポイント13から24は、配列のインデックス12から23に対応
    // HTML上では、右から左に描画するために逆順でループ
    for (let i = 23; i >= 12; i--) {
        if (i === 17) { // ポイント18の前 (インデックス17) にバーを挿入
            if (i === 17) { // ポイント18の前 (インデックス17) にバーを挿入
                const bar = document.createElement('div');
                bar.classList.add('bar');
                topRow.appendChild(bar);
            }
        }
        const pointElement = createPointElement(i);
        topRow.appendChild(pointElement);
    }
    boardElement.appendChild(topRow);

    // 下段のポイント (1-12) - 盤面の下半分
    const bottomRow = document.createElement('div');
    bottomRow.classList.add('board-row', 'bottom-row');
    // 盤面上のポイント1から12は、配列のインデックス0から11に対応
    // HTML上では、左から右に描画するために通常の順でループ
    for (let i = 0; i < 12; i++) {
        if (i === 6) { // ポイント7の前 (インデックス6) にバーを挿入
            const bar = document.createElement('div');
            bar.classList.add('bar');
            bottomRow.appendChild(bar);
        }
        const pointElement = createPointElement(i);
        bottomRow.appendChild(pointElement);
    }
    boardElement.appendChild(bottomRow);
}

function createPointElement(pointIndex) {
    const pointElement = document.createElement('div');
    pointElement.classList.add('point');
    pointElement.dataset.pointIndex = pointIndex;

    const pointData = points[pointIndex];
    if (pointData.count > 0) {
        const checkerStack = document.createElement('div');
        checkerStack.classList.add('checker-stack');
        for (let i = 0; i < pointData.count; i++) {
            const checker = document.createElement('div');
            checker.classList.add('checker');
            if (pointData.player === RED) {
                checker.classList.add('red');
            } else {
                checker.classList.add('white');
            }
            // 駒の重なりを表現するためにpositionを調整
            checker.style.top = `${i * 3}px`; 
            checkerStack.appendChild(checker);
        }
        pointElement.appendChild(checkerStack);
    }
    return pointElement;
}

function updateUI() {
    currentPlayerElement.textContent = currentPlayer === RED ? '赤' : '白';
    die1Element.textContent = dice[0] === 0 ? '' : dice[0];
    die2Element.textContent = dice[1] === 0 ? '' : dice[1];
    messageElement.textContent = '';
}

function rollDice() {
    dice[0] = Math.floor(Math.random() * 6) + 1;
    dice[1] = Math.floor(Math.random() * 6) + 1;
    updateUI();
    messageElement.textContent = `出目: ${dice[0]}, ${dice[1]}`;
    // ここで駒の移動ロジックを開始
}

rollDiceButton.addEventListener('click', rollDice);

// ゲーム開始
initializeGame();

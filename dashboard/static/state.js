// ── State ────────────────────────────────────────────────────────────────
let requestsData = [], activeFilter = null, lastDeniedCmd = '', policiesCache = {};
let _committedPolicy = null; // {exact, regexWhite, regexBlack} snapshot for the unsaved-changes banner
let _knownPendingIds = new Set();
let _knownBlockedIds = new Set();
let _knownAutoIds = new Set();
let _knownBlockedFlashIds = new Set();
let _knownWinReqIds = new Set();
let _expandedDescs = new Set();
const VIEWS = ['home', 'history', 'gateways', 'windows', 'stats', 'controls', 'fleet', 'integrations', 'settings', 'logs'];
const GW_COLORS = ['#e63946','#c0563a','#b87333','#d4a017','#7b8641','#7a9d54','#c4a45a','#8b6b7d'];
const GW_LABELS = ['Crimson','Terracotta','Copper','Amber','Olive','Sage','Sand','Plum'];
const DENY_BLOCKLIST_PROMPT_THRESHOLD = 10; // deny-bar only shows on the Nth denial of a command
let soundMuted = false;
let lastJitNotifyTime = 0;

// ── Sound & Notification State ──────────────────────────────────────────
let _audioCtx = null;
let _knownOfflineIps = new Set();
let notifyJIT = localStorage.getItem('notifyJIT') !== 'false';
let notifyBlocked = localStorage.getItem('notifyBlocked') === 'true';
let notifySound = localStorage.getItem('notifySound') !== 'false';
let notifyOffline = localStorage.getItem('notifyOffline') !== 'false';
let _notifPerm = 'default';
let _authChecked = false, _passwordSet = false;
let _allGateways = [], _devGateways = [];
let _recentJITData = [], _selectedJIT = [];
let _pendingWinReqs = [];
let _pendingIntegrationCalls = [];
let _knownIntegrationCallIds = new Set();
let _maxMcpCallId = 0;
let _mcpActivitySeeded = false;
let _lastAutoChimeAt = 0;
let _lastMcpChimeAt = 0;
let _winEditId = null;
let _winSource = 'jit';
let _winType = 'recurring';
let _winDays = 0;
let _winNeverExpire = true;
let _winHour = 0, _winMin = 0;
let _winMatchType = 'exact';
let _winGateways = [];
let _gwDropdownOpen = false;

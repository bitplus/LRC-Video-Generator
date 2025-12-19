const API_BASE = "/api";

// State
let config = {};
let filePaths = {
    audio: "",
    cover: "",
    lrc: "",
    background: ""
};
let audioDuration = 0;

// Init
document.addEventListener("DOMContentLoaded", () => {
    loadConfig();
    setupEventListeners();
});

async function loadConfig() {
    try {
        const res = await fetch(`${API_BASE}/config`);
        config = await res.json();
        
        // Populate dropdowns
        populateSelect('bg-anim', config.background_animations);
        populateSelect('text-anim', config.text_animations);
        populateSelect('cover-anim', config.cover_animations);
        populateSelect('hw-accel', config.hw_accels);
        
        populateSelect('font-primary', config.fonts);
        populateSelect('font-secondary', config.fonts);
        
        // Set defaults
        if (config.fonts.length > 0) {
             document.getElementById('font-primary').value = config.fonts[0];
             document.getElementById('font-secondary').value = config.fonts[0];
        }
        
    } catch (e) {
        console.error("Failed to load config", e);
        logMessage("Failed to connect to server.");
    }
}

function populateSelect(id, items) {
    const select = document.getElementById(id);
    select.innerHTML = '';
    items.forEach(item => {
        const option = document.createElement('option');
        option.value = item;
        option.textContent = item;
        select.appendChild(option);
    });
}

function setupEventListeners() {
    // File uploads
    setupFileUpload('audio-upload', 'audio', 'audio-path');
    setupFileUpload('cover-upload', 'cover', 'cover-path');
    setupFileUpload('lrc-upload', 'lrc', 'lrc-path');
    setupFileUpload('bg-upload', 'background', 'bg-path');
    setupFileUpload('project-upload', 'project', null);

    // Color inputs sync
    setupColorSync('color-primary', 'color-primary-text');
    setupColorSync('color-secondary', 'color-secondary-text');
    setupColorSync('outline-color', 'outline-color-text');

    // Preview slider
    const slider = document.getElementById('preview-slider');
    slider.addEventListener('input', (e) => {
        const percent = e.target.value;
        const time = (percent / 100) * audioDuration;
        document.getElementById('preview-time-display').textContent = time.toFixed(2) + "s";
    });
}

function setupFileUpload(inputId, type, textId) {
    document.getElementById(inputId).addEventListener('change', async (e) => {
        const file = e.target.files[0];
        if (!file) return;

        if (type === 'project') {
            handleProjectLoad(file);
            return;
        }

        const formData = new FormData();
        formData.append('file', file);
        formData.append('type', type);

        try {
            logMessage(`Uploading ${type}...`);
            const res = await fetch(`${API_BASE}/upload`, {
                method: 'POST',
                body: formData
            });
            
            if (!res.ok) {
                const errData = await res.json();
                throw new Error(errData.detail || "Upload failed");
            }
            
            const data = await res.json();
            
            filePaths[type] = data.filename; // Use the server-side filename
            if (textId) document.getElementById(textId).value = file.name;
            
            logMessage(`${type} uploaded: ${data.filename}`);

            if (type === 'audio') {
                getAudioDuration(data.filename);
            }
            if (type === 'cover' && !filePaths.background) {
                // If no background selected, logic might default to cover, 
                // but we don't set bg-path here to keep UI clean
            }
        } catch (e) {
            logMessage(`Upload failed: ${e.message}`);
        }
    });
}

function setupColorSync(colorId, textId) {
    const colorInput = document.getElementById(colorId);
    const textInput = document.getElementById(textId);

    colorInput.addEventListener('input', (e) => {
        textInput.value = e.target.value.toUpperCase();
    });
    
    // Optional: Text to Color sync could be added here
}

async function getAudioDuration(filename) {
    try {
        const res = await fetch(`${API_BASE}/audio-duration?path=${filename}&ffmpeg_path=${getVal('ffmpeg-path')}`);
        const data = await res.json();
        audioDuration = data.duration;
        logMessage(`Audio duration: ${audioDuration.toFixed(2)}s`);
    } catch (e) {
        console.error(e);
    }
}

function clearBg() {
    filePaths.background = "";
    document.getElementById('bg-path').value = "";
    document.getElementById('bg-upload').value = "";
}

function getVal(id) {
    return document.getElementById(id).value;
}

function gatherParams() {
    if (!filePaths.audio || !filePaths.cover || !filePaths.lrc) {
        alert("Please upload Audio, Cover, and LRC files first.");
        return null;
    }

    return {
        audio_path: filePaths.audio,
        cover_path: filePaths.cover,
        lrc_path: filePaths.lrc,
        background_path: filePaths.background || null,
        
        font_primary: getVal('font-primary'),
        font_size_primary: parseInt(getVal('font-size-primary')),
        font_secondary: getVal('font-secondary'),
        font_size_secondary: parseInt(getVal('font-size-secondary')),
        
        color_primary: getVal('color-primary'),
        color_secondary: getVal('color-secondary'),
        outline_color: getVal('outline-color'),
        outline_width: parseInt(getVal('outline-width')),
        
        background_anim: getVal('bg-anim'),
        text_anim: getVal('text-anim'),
        cover_anim: getVal('cover-anim'),
        
        ffmpeg_path: getVal('ffmpeg-path'),
        hw_accel: getVal('hw-accel'),
        
        preview_time: (parseFloat(getVal('preview-slider')) / 100) * audioDuration
    };
}

async function generatePreview() {
    const params = gatherParams();
    if (!params) return;

    logMessage("Starting preview generation...");
    document.querySelector('.preview-container').classList.add('loading');
    
    try {
        const res = await fetch(`${API_BASE}/preview`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(params)
        });
        const data = await res.json();
        
        pollTask(data.task_id, (result) => {
            const img = document.getElementById('preview-img');
            img.src = result.image_url + "?t=" + new Date().getTime(); // Prevent cache
            img.style.display = 'block';
            document.querySelector('.placeholder-text').style.display = 'none';
            document.querySelector('.preview-container').classList.remove('loading');
            logMessage("Preview generated.");
        });
        
    } catch (e) {
        logMessage(`Preview failed: ${e.message}`);
        document.querySelector('.preview-container').classList.remove('loading');
    }
}

async function startGeneration() {
    const params = gatherParams();
    if (!params) return;

    logMessage("Starting video generation...");
    document.getElementById('progress-bar').style.width = '0%';
    document.getElementById('progress-percent').textContent = '0%';
    
    try {
        const res = await fetch(`${API_BASE}/generate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(params)
        });
        const data = await res.json();
        
        pollTask(data.task_id, (result) => {
            logMessage("Video generation complete!");
            document.getElementById('progress-bar').style.width = '100%';
            document.getElementById('progress-percent').textContent = '100%';
            
            // Show modal
            const modal = document.getElementById('result-modal');
            const downloadLink = document.getElementById('download-link');
            downloadLink.href = result.video_url;
            downloadLink.download = result.filename;
            modal.style.display = 'flex';
        }, true);
        
    } catch (e) {
        logMessage(`Generation failed: ${e.message}`);
    }
}

async function pollTask(taskId, onSuccess, isVideo = false) {
    const interval = setInterval(async () => {
        try {
            const res = await fetch(`${API_BASE}/tasks/${taskId}`);
            const task = await res.json();
            
            if (task.status === 'completed') {
                clearInterval(interval);
                onSuccess(task.result);
            } else if (task.status === 'failed') {
                clearInterval(interval);
                logMessage(`Task failed: ${task.message}`);
                alert(`Task failed: ${task.message}`);
            } else {
                // Update logs
                if (task.logs && task.logs.length > 0) {
                     // We just append the last log for now or re-render all
                     // Better: check last log content
                     const lastLog = task.logs[task.logs.length - 1];
                     // Only log if it's new (simple check)
                     // logMessage(lastLog); 
                     // Actually, let's just update status text
                }
                
                if (isVideo) {
                    document.getElementById('progress-status').textContent = task.message;
                    document.getElementById('progress-percent').textContent = task.progress + "%";
                    document.getElementById('progress-bar').style.width = task.progress + "%";
                }
                
                // Append logs to log box
                const logsBox = document.getElementById('logs-box');
                logsBox.innerHTML = task.logs.join('\n');
                logsBox.scrollTop = logsBox.scrollHeight;
            }
        } catch (e) {
            console.error("Polling error", e);
        }
    }, 1000);
}

async function extractColors() {
    if (!filePaths.cover) {
        alert("Please upload a cover image first.");
        return;
    }
    
    try {
        logMessage("Extracting colors...");
        const res = await fetch(`${API_BASE}/extract-colors`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cover_path: filePaths.cover })
        });
        
        if (!res.ok) throw new Error(await res.text());
        
        const data = await res.json();
        
        updateColor('color-primary', data.color_primary);
        updateColor('color-secondary', data.color_secondary);
        updateColor('outline-color', data.outline_color);
        
        logMessage("Colors extracted successfully.");
    } catch (e) {
        logMessage(`Color extraction failed: ${e.message}`);
    }
}

function updateColor(id, value) {
    document.getElementById(id).value = value;
    document.getElementById(id + '-text').value = value;
}

function logMessage(msg) {
    const box = document.getElementById('logs-box');
    const time = new Date().toLocaleTimeString();
    box.innerHTML += `[${time}] ${msg}\n`;
    box.scrollTop = box.scrollHeight;
}

function toggleLogs() {
    const box = document.getElementById('logs-box');
    const icon = document.getElementById('log-toggle-icon');
    if (box.style.display === 'none') {
        box.style.display = 'block';
        icon.textContent = '▼';
    } else {
        box.style.display = 'none';
        icon.textContent = '▲';
    }
}

function saveProject() {
    const params = gatherParams();
    if (!params) return;
    
    // Convert to JSON and download
    const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(params, null, 2));
    const downloadAnchorNode = document.createElement('a');
    downloadAnchorNode.setAttribute("href", dataStr);
    downloadAnchorNode.setAttribute("download", "project.json");
    document.body.appendChild(downloadAnchorNode); // required for firefox
    downloadAnchorNode.click();
    downloadAnchorNode.remove();
}

function loadProject() {
    document.getElementById('project-upload').click();
}

function handleProjectLoad(file) {
    const reader = new FileReader();
    reader.onload = (e) => {
        try {
            const data = JSON.parse(e.target.result);
            applyProjectSettings(data);
            logMessage("Project loaded.");
        } catch (err) {
            logMessage("Failed to parse project file.");
        }
    };
    reader.readAsText(file);
}

function applyProjectSettings(data) {
    // We can't easily restore file paths because browser security prevents setting file inputs
    // But we can restore the internal state variables if the files are still on server (which they might not be in a new session)
    // For now, we just restore settings.
    
    if (data.file_paths) {
         // This assumes the format from the old app or new app
         // New app uses flat params
    }
    
    // Restore params
    const map = {
        'font-primary': data.font_primary,
        'font-size-primary': data.font_size_primary,
        'font-secondary': data.font_secondary,
        'font-size-secondary': data.font_size_secondary,
        'bg-anim': data.background_anim,
        'text-anim': data.text_anim,
        'cover-anim': data.cover_anim,
        'hw-accel': data.hw_accel,
        'ffmpeg-path': data.ffmpeg_path,
        'outline-width': data.outline_width
    };
    
    for (const [id, val] of Object.entries(map)) {
        if (val !== undefined && document.getElementById(id)) {
            document.getElementById(id).value = val;
        }
    }
    
    if (data.color_primary) updateColor('color-primary', data.color_primary);
    if (data.color_secondary) updateColor('color-secondary', data.color_secondary);
    if (data.outline_color) updateColor('outline-color', data.outline_color);
    
    logMessage("Settings restored. Please re-select files if needed.");
}

function closeModal() {
    document.getElementById('result-modal').style.display = 'none';
}

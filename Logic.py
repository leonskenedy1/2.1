import subprocess, sys, json, re, os, glob, time, shutil, argparse, tempfile

YTDLP_BASE = 'yt-dlp --cookies cookies.txt --js-runtimes deno --remote-components ejs:npm'
PROGRESS_TEMPLATE = "%(progress._percent_str)s of %(progress._total_bytes_str)s at %(progress._speed_str)s ETA %(progress._eta_str)s (frag %(progress.fragment_index)s/%(progress.fragment_count)s)"

def parse_duration(val, unit):
    v = float(val)
    if unit == 'h': return v * 3600.0
    if unit == 'm': return v * 60.0
    return v

def try_parse_delay(tokens, i):
    if i + 1 >= len(tokens):
        return None
    if re.match(r'^\d+(\.\d+)?$', tokens[i]) and tokens[i+1] in ('h','m','s'):
        return (parse_duration(tokens[i], tokens[i+1]), i + 2)
    return None

def select_formats(tasks_file):
    output = []
    with open(tasks_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            url = parts[0]
            if not ('youtube.com/watch?v=' in url or 'youtu.be/' in url):
                continue
            video_id = url.split('v=')[-1].split('/')[0] if 'v=' in url else url.split('youtu.be/')[-1].split('/')[0]
            opts = parts[1:] if len(parts) > 1 else ['v', 'max']

            tempfile = f'temp_{video_id}.json'
            try:
                subprocess.run(f'{YTDLP_BASE} --no-progress -j "{url}" > {tempfile}', shell=True, check=True, stderr=subprocess.PIPE)
            except subprocess.CalledProcessError as e:
                print(f'ERROR: yt-dlp failed for {url}: {e.stderr.decode()}')
                continue
            with open(tempfile) as jf:
                data = json.load(jf)
            title = data.get('title', video_id)
            formats = data.get('formats', [])
            combined = [f for f in formats if f.get('vcodec') != 'none' and f.get('acodec') != 'none']
            audio_only = [f for f in formats if f.get('vcodec') == 'none' and f.get('acodec') != 'none']

            groups = []
            i = 0
            first_group = True
            while i < len(opts):
                token = opts[i]
                if token in ('v', 'a'):
                    typ = token
                    i += 1
                    if i >= len(opts):
                        val1 = 'max'
                        val2 = ''
                    else:
                        val1 = opts[i]
                        i += 1
                        if typ == 'v':
                            if val1 == '2k': val1 = '1440'
                            elif val1 == '4k': val1 = '2160'
                        val2 = ''
                        if typ == 'v' and val1.isdigit():
                            if i < len(opts) and opts[i].isdigit():
                                next_is_fps = True
                                if i+1 < len(opts) and opts[i+1] in ('h','m','s'):
                                    next_is_fps = False
                                if next_is_fps:
                                    val2 = opts[i]
                                    i += 1
                    internal_delay = 0.0
                    if val1 == 'all' and i < len(opts) and opts[i].startswith('('):
                        expr = ''
                        while i < len(opts):
                            expr += opts[i] + ' '
                            if ')' in opts[i]:
                                break
                            i += 1
                        i += 1
                        expr_clean = expr.replace('(','').replace(')','').strip()
                        parts_d = expr_clean.split()
                        j = 0
                        while j < len(parts_d):
                            if j+1 < len(parts_d) and re.match(r'^\d+(\.\d+)?$', parts_d[j]) and parts_d[j+1] in ('h','m','s'):
                                internal_delay += parse_duration(parts_d[j], parts_d[j+1])
                                j += 2
                            else:
                                j += 1
                    delay_after = 0.0
                    while True:
                        res = try_parse_delay(opts, i)
                        if res is None:
                            break
                        delay_after += res[0]
                        i = res[1]
                    groups.append({
                        'type': typ,
                        'val1': val1,
                        'val2': val2,
                        'internal_delay': internal_delay,
                        'delay_after': delay_after
                    })
                    first_group = False

                elif token == 'all':
                    i += 1
                    internal_delay = 0.0
                    if i < len(opts) and opts[i].startswith('('):
                        expr = ''
                        while i < len(opts):
                            expr += opts[i] + ' '
                            if ')' in opts[i]:
                                break
                            i += 1
                        i += 1
                        expr_clean = expr.replace('(','').replace(')','').strip()
                        parts_d = expr_clean.split()
                        j = 0
                        while j < len(parts_d):
                            if j+1 < len(parts_d) and re.match(r'^\d+(\.\d+)?$', parts_d[j]) and parts_d[j+1] in ('h','m','s'):
                                internal_delay += parse_duration(parts_d[j], parts_d[j+1])
                                j += 2
                            else:
                                j += 1
                    delay_after = 0.0
                    while True:
                        res = try_parse_delay(opts, i)
                        if res is None:
                            break
                        delay_after += res[0]
                        i = res[1]
                    groups.append({'type': 'v', 'val1': 'all', 'val2': '', 'internal_delay': internal_delay, 'delay_after': 0})
                    groups.append({'type': 'a', 'val1': 'all', 'val2': '', 'internal_delay': internal_delay, 'delay_after': delay_after})
                    first_group = False
                else:
                    i += 1

            downloads = []
            for grp in groups:
                typ, val1, val2 = grp['type'], grp['val1'], grp.get('val2','')
                internal, da = grp['internal_delay'], grp['delay_after']
                if val1 == 'all':
                    fmts = sorted(
                        (combined if typ == 'v' else audio_only),
                        key=lambda x: (x.get('height', 0), x.get('fps', 0)) if typ == 'v' else (x.get('abr') or x.get('tbr') or 0),
                        reverse=True
                    )
                    if not fmts:
                        print(f'WARNING: No formats for {typ} all in {url}')
                        continue
                    for idx, f in enumerate(fmts):
                        fid = f['format_id']
                        delay = da if idx == len(fmts) - 1 else internal
                        downloads.append({'format_id': fid, 'type': typ, 'delay_after': delay})
                else:
                    if val1 == 'max':
                        fid = (max if typ == 'v' else max)(
                            combined if typ == 'v' else audio_only,
                            key=lambda x: (x.get('height', 0), x.get('fps', 0)) if typ == 'v' else (x.get('abr') or x.get('tbr') or 0)
                        )['format_id']
                    elif val1 == 'min':
                        fid = (min if typ == 'v' else min)(
                            combined if typ == 'v' else audio_only,
                            key=lambda x: (x.get('height', 0), x.get('fps', 0)) if typ == 'v' else (x.get('abr') or x.get('tbr') or 0)
                        )['format_id']
                    else:
                        if typ == 'v':
                            target_h = int(val1)
                            target_fps = int(val2) if val2 else 0
                            same_h = [f for f in combined if f.get('height') == target_h]
                            if not same_h:
                                closest = min(combined, key=lambda x: abs(x.get('height', 0) - target_h))
                                same_h = [f for f in combined if f.get('height') == closest['height']]
                            if target_fps > 0:
                                exact = [f for f in same_h if f.get('fps') == target_fps]
                                fid = exact[0]['format_id'] if exact else max(same_h, key=lambda x: x.get('fps', 0))['format_id']
                            else:
                                fid = max(same_h, key=lambda x: x.get('fps', 0))['format_id']
                        else:
                            target_br = int(val1)
                            best = min(audio_only, key=lambda x: abs((x.get('abr') or x.get('tbr') or 0) - target_br))
                            fid = best['format_id']
                    downloads.append({'format_id': fid, 'type': typ, 'delay_after': da})

            entry = {'url': url, 'video_id': video_id, 'title': title, 'downloads': downloads}
            output.append(entry)

    with open('selected_formats.json', 'w') as f:
        json.dump(output, f, indent=2)

    queue = []
    for entry in output:
        for dl in entry['downloads']:
            queue.append({
                'url': entry['url'],
                'title': entry['title'],
                'video_id': entry['video_id'],
                'format_id': dl['format_id'],
                'type': dl['type'],
                'delay_after': dl['delay_after']
            })
    if queue:
        queue[-1]['delay_after'] = 0.0

    with open('download_queue.json', 'w') as f:
        json.dump(queue, f, indent=2)

def download_and_manifest(tasks_file):
    shutil.rmtree('temp_downloads', ignore_errors=True)
    os.makedirs('temp_downloads', exist_ok=True)
    manifest = []

    with open(tasks_file) as f:
        tasks = [line.strip() for line in f if line.strip()]

    for idx, line in enumerate(tasks):
        parts = line.split()
        url = parts[0]
        if 'youtube.com/watch?v=' in url or 'youtu.be/' in url:
            continue

        print(f"[{idx+1}/{len(tasks)}] Downloading non-YouTube: {url}", flush=True)
        os.chdir('temp_downloads')
        tmp_name = 'downloaded_file.bin'
        try:
            subprocess.run(f'wget --tries=3 --progress=dot:giga -O "{tmp_name}" "{url}"', shell=True, check=True)
            if os.path.exists(tmp_name):
                manifest.append({
                    'url': url,
                    'is_youtube': False,
                    'video_id': '',
                    'title': os.path.splitext(tmp_name)[0],
                    'files': [{'filename': tmp_name, 'type': 'direct'}]
                })
        except subprocess.CalledProcessError:
            print(f"Download failed for {url}", flush=True)
        os.chdir('..')

    if os.path.exists('download_queue.json'):
        with open('download_queue.json') as f:
            queue = json.load(f)
        total_yt = len(queue)
        for yt_idx, item in enumerate(queue, 1):
            url = item['url']
            title = item['title']
            video_id = item['video_id']
            fid = item['format_id']
            ftype = item['type']
            delay_after = item['delay_after']

            long_type = 'video' if ftype == 'v' else 'audio'

            print(f"[{yt_idx}/{total_yt}] Downloading {long_type} format {fid} for {title}", flush=True)

            tmp_dir = tempfile.mkdtemp(dir='temp_downloads')
            out_template = os.path.join(tmp_dir, f"{title}_{fid}.%(ext)s")
            cmd = f'stdbuf -oL {YTDLP_BASE} -f {fid} -o "{out_template}" --progress-delta 1 --progress-template "{PROGRESS_TEMPLATE}" "{url}"'
            try:
                subprocess.run(cmd, shell=True, check=True)
            except subprocess.CalledProcessError:
                print(f"Download failed for {title} (format {fid})", flush=True)
                shutil.rmtree(tmp_dir)
                if yt_idx < total_yt and delay_after > 0:
                    mins = int(delay_after // 60)
                    secs = int(delay_after % 60)
                    print(f"Pausing for {mins} min {secs} sec...", flush=True)
                    time.sleep(delay_after)
                continue

            downloaded_files = os.listdir(tmp_dir)
            if downloaded_files:
                dl_file = downloaded_files[0]
                shutil.move(os.path.join(tmp_dir, dl_file), os.path.join('temp_downloads', dl_file))
                key = f"{url}|{title}"
                existing = next((e for e in manifest if e.get('url') == url and e.get('title') == title and e.get('is_youtube')), None)
                if existing:
                    existing['files'].append({'filename': dl_file, 'type': long_type})
                else:
                    manifest.append({
                        'url': url,
                        'is_youtube': True,
                        'video_id': video_id,
                        'title': title,
                        'files': [{'filename': dl_file, 'type': long_type}]
                    })
                print(f"  -> Registered: {dl_file}", flush=True)
            else:
                print("No file found after download!", flush=True)
            shutil.rmtree(tmp_dir)

            if yt_idx < total_yt and delay_after > 0:
                mins = int(delay_after // 60)
                secs = int(delay_after % 60)
                if mins > 0:
                    print(f"Pausing for {mins} min {secs} sec...", flush=True)
                else:
                    print(f"Pausing for {secs} sec...", flush=True)
                time.sleep(delay_after)

    with open('download_manifest.json', 'w') as f:
        json.dump(manifest, f, indent=2)
    print("Manifest saved successfully.", flush=True)

def remux_videos():
    if not os.path.exists('download_manifest.json'):
        print("No manifest, skipping remux.", flush=True)
        return
    with open('download_manifest.json') as f:
        manifest = json.load(f)
    if not manifest:
        print("Manifest is empty, skipping remux.", flush=True)
        return

    video_count = 0
    for entry in manifest:
        new_files = []
        for file_info in entry.get('files', []):
            fname = file_info['filename']
            src_path = os.path.join('temp_downloads', fname)
            if not os.path.isfile(src_path):
                print(f"Remux: file not found {src_path}, keeping entry.", flush=True)
                new_files.append(file_info)
                continue
            size = os.path.getsize(src_path)
            is_video = False
            if entry.get('is_youtube'):
                if file_info.get('type') == 'video':
                    is_video = True
            else:
                if file_info.get('type') == 'direct':
                    try:
                        probe = subprocess.run(['ffprobe', '-v', 'quiet', '-select_streams', 'v:0', '-show_entries', 'stream=codec_type', '-of', 'csv=p=0', src_path],
                                               capture_output=True, text=True)
                        if probe.returncode == 0 and 'video' in probe.stdout:
                            is_video = True
                    except:
                        pass

            if is_video and size > 7 * 1024 * 1024 * 1024:
                print(f"Splitting large video: {fname} ({size/1e9:.2f} GB)", flush=True)
                try:
                    dur_cmd = f'ffprobe -v error -select_streams v:0 -show_entries format=duration -of csv=p=0 "{src_path}"'
                    dur_str = subprocess.check_output(dur_cmd, shell=True).decode().strip()
                    duration = float(dur_str)
                    half = duration / 2.0
                    base_name, ext = os.path.splitext(fname)
                    part1 = f"{base_name}_01{ext}"
                    part2 = f"{base_name}_02{ext}"
                    split_cmd = f'ffmpeg -hide_banner -loglevel warning -stats -i "{src_path}" -c copy -to {half} "temp_downloads/{part1}" -ss {half} -c copy "temp_downloads/{part2}" -y'
                    subprocess.run(split_cmd, shell=True, check=True)
                    os.remove(src_path)
                    new_files.append({'filename': part1, 'type': 'video' if entry.get('is_youtube') else 'direct'})
                    new_files.append({'filename': part2, 'type': 'video' if entry.get('is_youtube') else 'direct'})
                    video_count += 2
                    print(f"Split successful: {part1} + {part2}", flush=True)
                except Exception as e:
                    print(f"Split failed for {fname}: {e}", flush=True)
                    new_files.append(file_info)
            else:
                if is_video:
                    os.chdir('temp_downloads')
                    print(f"Remuxing {fname}...", flush=True)
                    out = f"fixed_{fname}"
                    try:
                        subprocess.run(f'ffmpeg -hide_banner -loglevel warning -stats -i "{fname}" -c copy "{out}" -y', shell=True, check=True)
                        os.replace(out, fname)
                    except subprocess.CalledProcessError:
                        print(f"Remux failed for {fname}", flush=True)
                    os.chdir('..')
                new_files.append(file_info)
                if is_video:
                    video_count += 1
        entry['files'] = new_files

    if video_count == 0:
        print("No video files found in manifest, nothing to remux.", flush=True)
    else:
        print(f"Remux completed for {video_count} video files.", flush=True)

    with open('download_manifest.json', 'w') as f:
        json.dump(manifest, f, indent=2)

def create_zips():
    if not os.path.exists('download_manifest.json'):
        print("No manifest, skipping ZIP.", flush=True)
        return
    with open('download_manifest.json') as f:
        manifest = json.load(f)
    if not manifest:
        print("Manifest is empty, skipping ZIP.", flush=True)
        return

    os.makedirs('final_downloads', exist_ok=True)
    original_cwd = os.getcwd()

    for entry in manifest:
        if not entry.get('is_youtube'):
            for file_info in entry['files']:
                fname = file_info['filename']
                src = os.path.join('temp_downloads', fname)
                if not os.path.isfile(src):
                    print(f"Skipping ZIP for non-file: {fname}", flush=True)
                    continue
                os.chdir('temp_downloads')
                try:
                    subprocess.run(f'zip -s 99m -j "../final_downloads/{fname}.zip" "{fname}"', shell=True, check=True)
                    os.remove(fname)
                except subprocess.CalledProcessError:
                    print(f"ZIP failed for {fname}", flush=True)
                    if os.path.exists(fname):
                        os.remove(fname)
                os.chdir(original_cwd)
        else:
            title = entry['title']
            video_files = [f['filename'] for f in entry['files'] if f['type'] == 'video']
            audio_files = [f['filename'] for f in entry['files'] if f['type'] == 'audio']

            for ftype, file_list in (('videos', video_files), ('audios', audio_files)):
                if not file_list:
                    continue
                os.chdir('temp_downloads')
                valid = True
                for fname in file_list:
                    if not os.path.isfile(fname):
                        print(f"Skipping directory: {fname}", flush=True)
                        valid = False
                        break
                if not valid:
                    os.chdir(original_cwd)
                    continue
                try:
                    cmd = ['zip', '-s', '99m', '-j', f'../final_downloads/{title}_{ftype}.zip'] + file_list
                    subprocess.run(cmd, check=True)
                    for fname in file_list:
                        if os.path.exists(fname):
                            os.remove(fname)
                except subprocess.CalledProcessError:
                    print(f"ZIP failed for {title}_{ftype}", flush=True)
                    for fname in file_list:
                        if os.path.exists(fname):
                            os.remove(fname)
                os.chdir(original_cwd)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--select', action='store_true')
    parser.add_argument('--download', action='store_true')
    parser.add_argument('--remux', action='store_true')
    parser.add_argument('--zip', action='store_true')
    args = parser.parse_args()

    if args.select:
        select_formats('tasks.txt')
    elif args.download:
        download_and_manifest('tasks.txt')
    elif args.remux:
        remux_videos()
    elif args.zip:
        create_zips()
    else:
        print("Use one of: --select, --download, --remux, --zip")

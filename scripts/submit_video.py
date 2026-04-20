#!/usr/bin/env python3
"""
submit_video.py — Route video generation to the right tool based on type.

Routing:
  talking-head    → HeyGen (lip sync, avatar consistency)
  house-tour      → Veo via Vertex AI (cinematic, no characters)
  walk-person     → Veo via Vertex AI (person in environment)
  job-site        → Kling 3.0 (construction animation, photo reference)
  from-scratch    → Kling 3.0 T2V (text only, no reference)

Usage:
  python submit_video.py --type talking-head --prompt "Mike explains..." --image-url "https://..."
  python submit_video.py --type house-tour --prompt "Cinematic walkthrough..."
  python submit_video.py --type job-site --prompt "Construction site..." --image-url "https://..."
  python submit_video.py --type from-scratch --prompt "South Florida aerial..."

Environment variables required:
  HEYGEN_API_KEY      — for talking-head
  VEO_API_KEY         — for house-tour and walk-person (Vertex AI)
  KLING_API_KEY       — for job-site and from-scratch (Atlas Cloud or direct Kling)
  CLAUDE_KEY_4_CONTENT   — for prompt optimization (optional)
"""
import os, sys, json, argparse, requests, time
from datetime import datetime

HEYGEN_API_KEY = os.environ.get('HEYGEN_API_KEY', '')
VEO_API_KEY    = os.environ.get('VEO_API_KEY', '')
KLING_API_KEY  = os.environ.get('KLING_API_KEY', '')

KLING_BASE  = 'https://api.atlascloud.ai/v1'   # Atlas Cloud proxy — switch to api.klingai.com when direct key obtained
HEYGEN_BASE = 'https://api.heygen.com/v2'
VEO_BASE    = 'https://generativelanguage.googleapis.com/v1beta'  # Vertex AI / Gemini Veo

ROUTING = {
    'talking-head': 'heygen',
    'house-tour':   'veo',
    'walk-person':  'veo',
    'job-site':     'kling',
    'from-scratch': 'kling',
}

ASPECT_RATIOS = {
    'talking-head': '9:16',
    'house-tour':   '16:9',
    'walk-person':  '16:9',
    'job-site':     '16:9',
    'from-scratch': '16:9',
}


# ─── HeyGen ─────────────────────────────────────────────────────────────────

def submit_heygen(prompt, image_url=None, avatar_id=None):
    """Submit a talking head video to HeyGen."""
    if not HEYGEN_API_KEY:
        raise ValueError('HEYGEN_API_KEY not set')

    headers = {'X-Api-Key': HEYGEN_API_KEY, 'Content-Type': 'application/json'}

    # If image provided, use photo avatar endpoint
    if image_url:
        payload = {
            'video_inputs': [{
                'character': {
                    'type': 'talking_photo',
                    'talking_photo_url': image_url,
                },
                'voice': {
                    'type': 'text',
                    'input_text': prompt,
                    'voice_id': 'en-US-GuyNeural',  # default — can be overridden
                }
            }],
            'dimension': {'width': 720, 'height': 1280},  # 9:16
            'aspect_ratio': '9_16',
        }
    else:
        payload = {
            'video_inputs': [{
                'character': {'type': 'avatar', 'avatar_id': avatar_id or 'default'},
                'voice': {'type': 'text', 'input_text': prompt, 'voice_id': 'en-US-GuyNeural'},
            }],
            'dimension': {'width': 720, 'height': 1280},
        }

    r = requests.post(f'{HEYGEN_BASE}/video/generate', headers=headers, json=payload)
    r.raise_for_status()
    data = r.json()
    video_id = data.get('data', {}).get('video_id') or data.get('video_id')
    print(f'[HeyGen] Submitted. video_id={video_id}')
    return {'tool': 'heygen', 'job_id': video_id, 'status': 'processing'}


# ─── Veo / Vertex AI ─────────────────────────────────────────────────────────

def submit_veo(prompt, image_url=None, aspect_ratio='16:9'):
    """Submit to Veo 2 via Gemini API."""
    if not VEO_API_KEY:
        raise ValueError('VEO_API_KEY not set')

    url = f'{VEO_BASE}/models/veo-2.0-generate-001:generateVideo'
    headers = {'Content-Type': 'application/json', 'x-goog-api-key': VEO_API_KEY}

    payload = {
        'prompt': {'text': prompt},
        'generationConfig': {
            'aspectRatio': aspect_ratio.replace(':', '_'),
            'durationSeconds': 8,
        }
    }

    if image_url:
        # Image-to-video
        payload['image'] = {'url': image_url}

    r = requests.post(url, headers=headers, json=payload)
    r.raise_for_status()
    data = r.json()
    op_name = data.get('name', '')
    print(f'[Veo] Submitted. operation={op_name}')
    return {'tool': 'veo', 'job_id': op_name, 'status': 'processing'}


# ─── Kling ───────────────────────────────────────────────────────────────────

def submit_kling(prompt, image_url=None, video_type='from-scratch', aspect_ratio='16:9'):
    """Submit to Kling 3.0 via Atlas Cloud (switch to direct API when available)."""
    if not KLING_API_KEY:
        raise ValueError('KLING_API_KEY not set')

    headers = {'Authorization': f'Bearer {KLING_API_KEY}', 'Content-Type': 'application/json'}

    mode = 'i2v' if image_url else 't2v'
    endpoint = f'{KLING_BASE}/videos/{mode}'

    payload = {
        'model_name': 'kling-v3',
        'prompt': prompt,
        'duration': '5',
        'aspect_ratio': aspect_ratio.replace(':', '_'),
        'mode': 'standard',
    }
    if image_url:
        payload['image_url'] = image_url

    r = requests.post(endpoint, headers=headers, json=payload)
    r.raise_for_status()
    data = r.json()
    task_id = data.get('data', {}).get('task_id') or data.get('task_id')
    print(f'[Kling] Submitted. task_id={task_id}')
    return {'tool': 'kling', 'job_id': task_id, 'status': 'processing'}


# ─── Router ──────────────────────────────────────────────────────────────────

def submit_video(video_type, prompt, image_url=None, avatar_id=None):
    """Route to correct tool and submit."""
    tool = ROUTING.get(video_type)
    if not tool:
        raise ValueError(f'Unknown video type: {video_type}. Valid: {list(ROUTING.keys())}')

    aspect = ASPECT_RATIOS.get(video_type, '16:9')
    print(f'[router] {video_type} → {tool} ({aspect})')

    if tool == 'heygen':
        return submit_heygen(prompt, image_url=image_url, avatar_id=avatar_id)
    elif tool == 'veo':
        return submit_veo(prompt, image_url=image_url, aspect_ratio=aspect)
    elif tool == 'kling':
        return submit_kling(prompt, image_url=image_url, video_type=video_type, aspect_ratio=aspect)


def main():
    parser = argparse.ArgumentParser(description='Submit video generation job')
    parser.add_argument('--type', required=True, choices=list(ROUTING.keys()),
                        help='Video type determines which tool is used')
    parser.add_argument('--prompt', required=True, help='Video generation prompt')
    parser.add_argument('--image-url', default=None, help='Reference image URL (for I2V)')
    parser.add_argument('--avatar-id', default=None, help='HeyGen avatar ID (optional)')
    args = parser.parse_args()

    result = submit_video(
        video_type=args.type,
        prompt=args.prompt,
        image_url=args.image_url,
        avatar_id=args.avatar_id,
    )

    print(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    main()

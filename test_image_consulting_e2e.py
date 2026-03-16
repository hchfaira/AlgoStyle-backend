#!/usr/bin/env python3
"""
End-to-End test for image-consulting API
Tests real image uploads from LLM_project/data/people
Usage: python test_image_consulting_e2e.py
"""
import sys
import os
from pathlib import Path
import json
import httpx
import time

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))

# Configuration
API_BASE_URL = "http://localhost:8000"
LLM_PROJECT_PATH = Path(__file__).parent.parent / "LLM_project"
TEST_IMAGES_DIR = LLM_PROJECT_PATH / "data" / "people"

TEST_USER = {
    "email": "test@algostyle.com",
    "password": "Test123!"
}


class AlgoStyleClient:
    """Simple HTTP client for AlgoStyle API"""
    
    def __init__(self, base_url: str = API_BASE_URL):
        self.base_url = base_url
        self.client = httpx.Client(timeout=30.0)
        self.token = None
    
    def login(self, email: str, password: str) -> dict:
        """Login and get access token"""
        response = self.client.post(
            f"{self.base_url}/api/v1/auth/login",
            json={"email": email, "password": password}
        )
        if response.status_code != 200:
            raise Exception(f"Login failed: {response.text}")
        data = response.json()
        self.token = data.get("access_token")
        return data
    
    def analyze_image(self, user_id: str, image_path: Path, height_cm: float = 170, weight_kg: float = 65) -> dict:
        """Upload image and run analysis"""
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        
        with open(image_path, "rb") as f:
            files = {
                "image": (image_path.name, f, "image/jpeg")
            }
            data = {
                "height_cm": str(height_cm),
                "weight_kg": str(weight_kg)
            }
            response = self.client.post(
                f"{self.base_url}/api/v1/image-consulting/analyze/{user_id}",
                headers=headers,
                files=files,
                data=data
            )
        
        if response.status_code not in [200, 201]:
            raise Exception(f"Analysis failed: {response.status_code} - {response.text}")
        return response.json()
    
    def get_result(self, user_id: str) -> dict:
        """Get cached analysis result"""
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        response = self.client.get(
            f"{self.base_url}/api/v1/image-consulting/result/{user_id}",
            headers=headers
        )
        if response.status_code != 200:
            raise Exception(f"Get result failed: {response.status_code}")
        return response.json()
    
    def close(self):
        """Close HTTP client"""
        self.client.close()


def find_test_images() -> list:
    """Find available test images"""
    if not TEST_IMAGES_DIR.exists():
        print(f"⚠️  Images directory not found: {TEST_IMAGES_DIR}")
        return []
    
    images = list(TEST_IMAGES_DIR.glob("*.jpg")) + list(TEST_IMAGES_DIR.glob("*.png"))
    return images[:3]  # Limit to 3 images for testing


def pretty_print_result(result: dict):
    """Pretty print analysis result"""
    print("\n📊 Analysis Result:")
    print("=" * 60)
    
    # Basic profile
    print(f"👤 Profile:")
    print(f"   Body Shape: {result.get('body_shape', '?')}")
    print(f"   Face Shape: {result.get('face_shape', '?')}")
    print(f"   Skin Tone: {result.get('skin_tone', '?')}")
    print(f"   Undertone: {result.get('undertone', '?')}")
    print(f"   Hair Color: {result.get('hair_color', '?')}")
    print(f"   Contrast: {result.get('contrast_level', '?')}")
    print(f"   Visual Weight: {result.get('visual_weight', '?')}")
    
    # Color season
    print(f"\n🎨 Color Season: {result.get('color_season', '?')}")
    
    # Sizes
    if result.get('estimated_top_size') or result.get('estimated_bottom_size'):
        print(f"\n📏 Sizes:")
        print(f"   Tops: {result.get('estimated_top_size', '?')}")
        print(f"   Bottoms: {result.get('estimated_bottom_size', '?')}")
    
    # Summary
    if result.get('summary'):
        print(f"\n💬 Summary:")
        print(f"   {result['summary']}")
    
    # Confidence
    confidence = result.get('overall_confidence', 0)
    print(f"\n✨ Confidence: {confidence * 100:.1f}%")
    
    print("=" * 60)


def main():
    """Main test function"""
    print("🧪 AlgoStyle Image Consulting — End-to-End Test")
    print("=" * 60)
    
    # Check if API is running
    print("\n🔌 Checking API connection...")
    try:
        response = httpx.get(f"{API_BASE_URL}/docs", timeout=5)
        if response.status_code == 200:
            print(f"✅ API is running on {API_BASE_URL}")
        else:
            print(f"❌ API not responding correctly")
            return
    except Exception as e:
        print(f"❌ Cannot connect to API: {e}")
        print(f"   Make sure backend is running: uvicorn main:app --reload")
        return
    
    # Find test images
    print("\n🖼️  Looking for test images...")
    test_images = find_test_images()
    if not test_images:
        print(f"⚠️  No test images found in {TEST_IMAGES_DIR}")
        print("   Copy images from LLM_project/data/people/ to test")
        return
    
    print(f"✅ Found {len(test_images)} test image(s)")
    
    # Initialize client
    client = AlgoStyleClient()
    
    try:
        # Login
        print("\n🔐 Logging in...")
        user_data = client.login(TEST_USER["email"], TEST_USER["password"])
        user_id = user_data.get("user_id")
        print(f"✅ Logged in as {TEST_USER['email']}")
        print(f"   User ID: {user_id}")
        
        # Test each image
        for i, image_path in enumerate(test_images, 1):
            print(f"\n📸 Test {i}: {image_path.name}")
            print("-" * 60)
            
            try:
                print(f"   Uploading and analyzing...")
                start_time = time.time()
                
                result = client.analyze_image(
                    user_id,
                    image_path,
                    height_cm=170,
                    weight_kg=65
                )
                
                elapsed = time.time() - start_time
                print(f"   ✅ Analysis complete in {elapsed:.1f}s")
                
                pretty_print_result(result)
                
                # Save result to file
                result_file = image_path.parent / f"{image_path.stem}_result.json"
                with open(result_file, "w") as f:
                    json.dump(result, f, indent=2, default=str)
                print(f"\n   📄 Result saved to: {result_file}")
                
            except Exception as e:
                print(f"   ❌ Error: {e}")
                continue
        
        print("\n" + "=" * 60)
        print("✅ All tests complete!")
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        client.close()


if __name__ == "__main__":
    main()

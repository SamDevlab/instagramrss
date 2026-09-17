from instagram.collector import StoryCollector


def test_profile_images_are_not_treated_as_story_media():
    assert StoryCollector._is_profile_image(
        "https://cdn.instagram.com/v/t51.89012-19/profile.jpg"
    )
    assert not StoryCollector._is_profile_image(
        "https://cdn.instagram.com/v/t51.82787-15/story.jpg"
    )


def test_video_fragments_share_one_story_identity():
    first = "https://cdn.instagram.com/o1/v/t2/story.mp4?bytestart=0&byteend=99"
    second = "https://cdn.instagram.com/o1/v/t2/story.mp4?bytestart=100&byteend=199"
    assert StoryCollector._media_key("video", first) == StoryCollector._media_key("video", second)

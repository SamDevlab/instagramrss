from instagram.collector import StoryCollector


def test_profile_images_are_not_treated_as_story_media():
    assert StoryCollector._is_profile_image(
        "https://cdn.instagram.com/v/t51.89012-19/profile.jpg"
    )
    assert not StoryCollector._is_profile_image(
        "https://cdn.instagram.com/v/t51.82787-15/story.jpg"
    )

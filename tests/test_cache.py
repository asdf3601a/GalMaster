from app.translate.cache import TranslationCache


def test_cache_put_get():
    c = TranslationCache(max_size=2)
    k1 = TranslationCache.make_key("hello", "en", "zh-Hant", "m")
    k2 = TranslationCache.make_key("world", "en", "zh-Hant", "m")
    k3 = TranslationCache.make_key("third", "en", "zh-Hant", "m")
    c.put(k1, "你好")
    assert c.get(k1) == "你好"
    c.put(k2, "世界")
    c.put(k3, "第三")  # evicts k1
    assert c.get(k1) is None
    assert c.get(k2) == "世界"
    assert c.get(k3) == "第三"


def test_cache_key_differs_by_lang():
    a = TranslationCache.make_key("x", "ja", "zh-Hant", "m")
    b = TranslationCache.make_key("x", "ja", "en", "m")
    assert a != b

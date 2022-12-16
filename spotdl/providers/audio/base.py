"""
Base audio provider module.
"""

import re

from typing import Any, Dict, List, Optional, Tuple
from itertools import zip_longest

from rapidfuzz import fuzz
from yt_dlp import YoutubeDL

from spotdl.types import Song
from spotdl.types.result import Result
from spotdl.utils.config import get_temp_path
from spotdl.utils.formatter import (
    create_song_title,
    create_search_query,
    slugify,
)


class AudioProviderError(Exception):
    """
    Base class for all exceptions related to audio searching/downloading.
    """


class YTDLLogger:
    """
    Custom YT-dlp logger.
    """

    def debug(self, msg):  # pylint: disable=R0201
        """
        YTDL uses this to print debug messages.
        """

        pass  # pylint: disable=W0107

    def warning(self, msg):  # pylint: disable=R0201
        """
        YTDL uses this to print warnings.
        """

        pass  # pylint: disable=W0107

    def error(self, msg):  # pylint: disable=R0201
        """
        YTDL uses this to print errors.
        """

        raise AudioProviderError(msg)


ISRC_REGEX = re.compile(r"^[A-Z]{2}-?\w{3}-?\d{2}-?\d{5}$")


class AudioProvider:
    """
    Base class for all other providers. Provides some common functionality.
    Handles the yt-dlp audio handler.
    """

    SUPPORTS_ISRC: bool
    GET_RESULTS_OPTS: List[Dict[str, Any]]

    def __init__(
        self,
        output_format: str = "mp3",
        cookie_file: Optional[str] = None,
        search_query: Optional[str] = None,
        filter_results: bool = True,
    ) -> None:
        """
        Base class for audio providers.

        ### Arguments
        - output_directory: The directory to save the downloaded songs to.
        - output_format: The format to save the downloaded songs in.
        - cookie_file: The path to a file containing cookies to be used by YTDL.
        - search_query: The query to use when searching for songs.
        - filter_results: Whether to filter results.

        ### Errors
        - raises `NotImplementedError` if self.name is not set.
        """

        self.output_format = output_format
        self.cookie_file = cookie_file
        self.search_query = search_query
        self.filter_results = filter_results

        if self.output_format == "m4a":
            ytdl_format = "bestaudio[ext=m4a]/bestaudio/best"
        elif self.output_format == "opus":
            ytdl_format = "bestaudio[ext=webm]/bestaudio/best"
        else:
            ytdl_format = "bestaudio"

        self.audio_handler = YoutubeDL(
            {
                "format": ytdl_format,
                "quiet": True,
                "no_warnings": True,
                "encoding": "UTF-8",
                "logger": YTDLLogger(),
                "cookiefile": self.cookie_file,
                "outtmpl": f"{get_temp_path()}/%(id)s.%(ext)s",
                "retries": 5,
            }
        )

    def get_results(self, search_term: str, **kwargs) -> List[Result]:
        """
        Get results from audio provider.

        ### Arguments
        - search_term: The search term to use.
        - kwargs: Additional arguments.

        ### Returns
        - A list of results.
        """

        raise NotImplementedError

    def get_views(self, url: str) -> int:
        """
        Get the number of views for a video.

        ### Arguments
        - url: The url of the video.

        ### Returns
        - The number of views.
        """

        data = self.get_download_metadata(url)

        return data["view_count"]

    def search(self, song: Song) -> Optional[str]:
        """
        Search for a song and return best match.

        ### Arguments
        - song: The song to search for.

        ### Returns
        - The url of the best match or None if no match was found.
        """

        # Create initial search query
        search_query = create_song_title(song.name, song.artists).lower()

        if self.search_query:
            search_query = create_search_query(
                song, self.search_query, False, None, True
            )
        elif song.isrc and self.SUPPORTS_ISRC:
            # search for song using isrc if it's available
            if song.isrc:
                isrc_results = self.get_results(
                    song.isrc, filter="songs", ignore_spelling=True
                )

                if len(isrc_results) == 1:
                    sorted_isrc_results = self.order_results([isrc_results[0]], song)
                    if len(sorted_isrc_results) == 1:
                        isrc_result, isrc_score = sorted_isrc_results.popitem()

                        if isrc_score > 90:
                            # print(f"# RETURN URL - {isrc_result.url} - isrc score")
                            return isrc_result.url

                # print(f"# no match found for isrc {song.name} - {song.isrc}")

        results: Dict[Result, float] = {}
        for options in self.GET_RESULTS_OPTS:
            # Query YTM by songs only first, this way if we get correct result on the first try
            # we don't have to make another request
            search_results = self.get_results(search_query, **options)

            if self.filter_results:
                # Order results
                new_results = self.order_results(search_results, song)
            else:
                new_results = {}
                if len(new_results) > 0:
                    new_results = {search_results[0]: 100.0}

            # song type results are always more accurate than video type,
            # so if we get score of 80 or above
            # we are almost 100% sure that this is the correct link
            if len(new_results) != 0:
                # get the result with highest score
                best_url, best_score = self.get_best_match(new_results)

                if best_score >= 80:
                    # print(f"# RETURN URL - {best_url} - score >= 80")
                    return best_url

                # Update final results with new results
                results.update(new_results)

        # No matches found
        if not results:
            return None

        # get the result with highest score
        best_url, best_score = self.get_best_match(results)

        # print(f"# RETURN URL - {best_url} /w video results - score {best_score}")

        return best_url

    def order_results(self, results: List[Result], song: Song) -> Dict[Result, float]:
        """
        Order results.

        ### Arguments
        - results: The results to order.
        - song: The song to order for.

        ### Returns
        - The ordered results.
        """

        # Slugify some variables
        slug_song_name = slugify(song.name)
        sentence_words = slug_song_name.split("-")
        slug_song_album_name = slugify(song.album_name)
        slug_song_main_artist = slugify(song.artist)
        slug_song_artists = slugify(", ".join(song.artists))
        slug_song_title = slugify(
            create_song_title(song.name, song.artists)
            if not self.search_query
            else create_search_query(song, self.search_query, False, None, True)
        )

        # DEBUG CODE
        # print("#############################")
        # print(f"song.name: {song.name}")
        # print(f"song.album_name: {song.album_name}")
        # print(f"song.artist: {song.artist}")
        # print(f"song.artists: {song.artists}")
        # print(f"song.isrc: {song.isrc}")
        # print(f"song.duration: {song.duration}")
        # print(f"slug_song_name: {slug_song_name}")
        # print(f"slug_song_album_name: {slug_song_album_name}")
        # print(f"slug_song_main_artist: {slug_song_main_artist}")
        # print(f"slug_song_artists: {slug_song_artists}")
        # print(f"slug_song_title: {slug_song_title}")
        # print(f"slug_song_duration: {song.duration}")
        # print(f"slug_song_artists: {slug_song_artists}")
        # print(f"sentence_words: {sentence_words}")

        # Assign an overall avg match value to each result
        links_with_match_value = {}
        for result in results:
            # Slugify result title
            slug_result_name = slugify(result.name)
            slug_result_artists = (
                slugify(", ".join(result.artists)) if result.artists else ""
            )
            slug_result_album = slugify(result.album) if result.album else None

            # check for common words in result name
            common_word = any(
                word != "" and word in slug_result_name for word in sentence_words
            )

            test_str1 = slug_result_name
            test_str2 = slug_song_name if result.verified else slug_song_title

            # check if the artist is in the song name
            # but not in the result name
            # if it is, we add the artist to the result name
            for artist in song.artists:
                slug_song_artist = slugify(artist).replace("-", "")
                if slug_song_artist in test_str2.replace(
                    "-", ""
                ) and slug_song_artist not in test_str1.replace("-", ""):
                    test_str1 += f"-{slug_song_artist}"

            # same thing for for song name
            for artist in song.artists:
                slug_result_artist = slugify(artist).replace("-", "")
                if slug_result_artist in test_str1.replace(
                    "-", ""
                ) and slug_result_artist not in test_str2.replace("-", ""):
                    test_str2 += f"-{slug_result_artist}"

            test_str1_list = test_str1.split("-")
            test_str2_list = test_str2.split("-")

            test_str1_list.sort()
            test_str2_list.sort()

            test_str1 = "-".join(test_str1_list)
            test_str2 = "-".join(test_str2_list)

            # print("-----------------------------")
            # print(f"common_word: {common_word}")
            # print(f"result link: {result.url}")
            # print(f"result name: {result.name}")
            # print(f"result type: {result.verified}")
            # print(f"result duration: {result.duration}")
            # print(f"result artists_list: {result.artists}")
            # print(f"slug_result_name: {slug_result_name}")
            # print(f"slug_result_artists: {slug_result_artists}")
            # print(f"slug_result_album: {slug_result_album}")
            # print(f"test_str1: {test_str1}")
            # print(f"test_str2: {test_str2}")
            # print("-----------------------------")

            # skip results that have no common words in their name
            if not common_word:
                # print("! common_word is False")
                continue

            # initialize match value to 0
            main_artist_match = 0.0

            # check if artists list is not empty
            # if it isn't perform initial match
            # on the main artists
            if result.artists:
                main_artist_match = fuzz.ratio(
                    slug_song_main_artist, slugify(result.artists[0])
                )

            # print(f"? main_artist_match: {main_artist_match}")

            artist_match_number = main_artist_match
            if len(song.artists) > 1:
                # match the song's artists with the result's artists

                if result.artists and len(song.artists) == len(result.artists):
                    artists_match = fuzz.ratio(slug_song_artists, slug_result_artists)
                    # print(f"exact artists_match: {artists_match}")
                else:
                    artists_match = artist_match_number
                    if (
                        result.artists
                        and (len(result.artists) * 100) / len(song.artists) > 60
                        and len(song.artists) > 2
                    ):
                        # Sort list1
                        artist1_list = list(map(slugify, song.artists))
                        artist1_list.sort()

                        # Sort list2
                        artist2_list = list(map(slugify, result.artists))
                        artist2_list.sort()

                        # print(f"artist1_list: {artist1_list}")
                        # print(f"artist2_list: {artist2_list}")

                        list_map = {
                            value: index for index, value in enumerate(artist2_list)
                        }
                        # print(f"list_map: {list_map}")

                        # Sort list2 based on list1
                        artist1_list = sorted(
                            artist1_list,
                            key=lambda x: list_map.get(x, -1),
                            reverse=True,
                        )

                        # print(f"artist1_list after sorting: {artist1_list}")

                        artists_match = 0.0
                        for artist1, artist2 in zip_longest(artist1_list, artist2_list):
                            artists_match += fuzz.ratio(artist1, artist2)

                        artists_match = artists_match / len(artist1_list)

                        # print(f"artists_match: {artists_match}/{len(artist2_list)})")

                artist_match_number += artists_match

            artist_match = artist_match_number / (2 if len(song.artists) > 1 else 1)
            # print("? first artist_match: ", artist_match)

            # additional checks for results that are not songs
            if artist_match <= 50 and not result.verified:
                # If we didn't find any artist match,
                # we fallback to channel name match
                channel_name_match = fuzz.ratio(
                    slugify(song.artist),
                    slug_result_artists,
                )

                if channel_name_match > artist_match_number:
                    artist_match = channel_name_match
                    # print("? second artist_match: ", artist_match)

                # If artist match is still too low,
                # we fallback to matching all song artist names
                # with the result's title
                if artist_match <= 50:
                    artist_title_match = 0.0
                    for artist in song.artists:
                        slug_artist = slugify(artist).replace("-", "")
                        if slug_artist in slug_result_name.replace("-", ""):
                            artist_title_match += 1.0

                    artist_title_match = (artist_title_match / len(song.artists)) * 100
                    # print(f"? artist_title_match: {artist_title_match}")

                    if artist_title_match > artist_match:
                        artist_match = artist_title_match
                        # print("? third artist_match: ", artist_match)

            # additional checks for results that are songs
            if artist_match < 70 and result.verified:
                # Check if the song name is very similar to the result name
                if (
                    fuzz.ratio(
                        test_str1,
                        test_str2,
                    )
                    >= 75
                ):
                    # If it is, we increase the artist match
                    artist_match += 10
                    # print("? song name artist_match: ", artist_match)

                    # if the result doesn't have the same number of artists but has
                    # the same main artist and similar name
                    # we add 25% to the artist match
                    if (
                        result.artists
                        and len(result.artists) < len(song.artists)
                        and slug_song_main_artist.replace("-", "")
                        in [
                            slug_result_artists.replace("-", ""),
                            slug_result_name.replace("-", ""),
                        ]
                    ):
                        artist_match += 25
                        # print("? hacky artist_match: ", artist_match)

                # Check if the song album name is very similar to the result album name
                # if it is, we increase the artist match
                if slug_result_album:
                    if fuzz.ratio(slug_result_album, slug_song_album_name) >= 85:
                        artist_match += 10
                        # print("? album artist_match: ", artist_match)

                # Check if other song artists are in the result name
                # if they are, we increase the artist match
                # (main artist is already checked, so we skip it)
                artists_to_check = (
                    song.artists[1:] if main_artist_match > 50 else song.artists
                )
                for artist in artists_to_check:
                    slug_song_artist = slugify(artist).replace("-", "")
                    if slug_song_artist in test_str2.replace("-", ""):
                        artist_match += 15 if len(song.artists[1:]) <= 2 else 10
                        # print("? other artist artist_match: ", artist_match)

                # if the artist match is still too low,
                # we fallback to matching all song artist names
                # with the result's artists
                if artist_match <= 70:
                    # artists from title without title words
                    clean_title1 = [
                        artist
                        for artist in map(slugify, song.artists)
                        if artist.replace("-", "")
                        not in slug_song_name.replace("-", "")
                    ]

                    # artists from result name without title words
                    clean_title2 = [
                        artist
                        for artist in map(
                            slugify,
                            result.artists if result.artists else [result.author],
                        )
                        if artist.replace("-", "")
                        not in slug_result_name.replace("-", "")
                    ]

                    clean_title1.sort()
                    clean_title2.sort()

                    # print(f"clean_title1: {clean_title1}")
                    # print(f"clean_title2: {clean_title2}")

                    artist_title_match = fuzz.ratio(
                        "-".join(clean_title1),
                        "-".join(clean_title2),
                    )

                    if artist_title_match > artist_match:
                        artist_match = artist_title_match
                        # print("? fourth artist_match: ", artist_match)

            # print("? final artist_match: ", artist_match)

            # skip results with artist match lower than 70%
            if artist_match < 70:
                # print("! artist_match < 70 - skipping")
                continue

            # check if the artist match is higher than 100%
            # if it is, we set it to 100% (this shouldn't happen)
            artist_match = min(artist_match, 100)

            # Calculate name match
            if artist_match >= 75:
                name_match = fuzz.ratio(
                    test_str1,
                    test_str2,
                )
            else:
                name_match = fuzz.ratio(
                    slug_result_name,
                    slug_song_name,
                )

            # Drop results with name match lower than 50%
            # print(f"name_match: {name_match}")
            if name_match < 50:
                # print("! name_match < 50 - skipping")
                continue

            # Find album match
            album_match = 0.0

            # Calculate album match only for songs
            if result.verified:
                if slug_result_album:
                    album_match = fuzz.ratio(slug_result_album, slug_song_album_name)

            # Calculate time match
            delta = result.duration - song.duration
            non_match_value = (delta**2) / song.duration * 100
            time_match = 100 - non_match_value

            # print(f"? time_match: {time_match}")

            # Calculate total match
            average_match = (artist_match + name_match) / 2

            # print(f"? album_match: {album_match}")
            # print(f"? time_match: {time_match}")
            # print(f"? average_match (only artist and name): {average_match}")

            if (
                result.verified
                and slug_result_album
                and average_match > 80
                and time_match > 80
            ):
                # we are almost certain that this is the correct result
                # so we add the album match to the average match
                average_match = average_match + album_match / 2

                # print(f"? average_match with album_match: {average_match}")

            if time_match < 50 and average_match < 85:
                # If the time match is lower than 50% and the average match is lower than 85%
                # we skip the result
                # print("! time_match < 50 and average_match < 85 - skipping")
                continue

            if time_match < 50 and not result.isrc_search:
                # If the time match is lower than 50% but the average match is higher than 85%
                # we add time match to the average match

                # if the result is an isrc result
                # or has really good average/album match
                # we don't add time match
                if average_match < 85 and album_match <= 75:
                    average_match = (average_match + time_match) / 2
                    # print(f"? average_match with time_match, not isrc: {average_match}")

            # print(f"? final average_match: {average_match}")

            # the results along with the avg Match
            links_with_match_value[result] = average_match

        return links_with_match_value

    def get_best_match(self, results: Dict[Result, float]) -> Tuple[str, float]:
        """
        Get the best match from the results
        using views and average match

        ### Arguments
        - results: A dictionary of results and their scores

        ### Returns
        - The best match URL and its score
        """

        result_items = list(results.items())

        # Sort results by highest score
        sorted_results = sorted(result_items, key=lambda x: x[1], reverse=True)

        last_simlar_index = 1
        best_score = sorted_results[0][1]

        # Get few results with score close to the best score
        for index, (_, score) in enumerate(sorted_results):
            if (best_score - score) > 8:
                last_simlar_index = index
                break

        # print(f"# last_simlar_index: {last_simlar_index}")
        # print(f"# sorted_results: {sorted_results}")

        # Get the best results from the similar results
        best_results = sorted_results[:last_simlar_index]

        # If we have only one result, return it
        if len(best_results) == 1:
            # print(f"# get_best_match URL - {sorted_results[0][0]} - only 1 result")
            return sorted_results[0][0].url, sorted_results[0][1]

        # print(f"# best results: {best_results}")

        # If we have more than one result,
        # return the one with the highest score
        # and most views
        views_data = [
            best_result[0].views
            if best_result[0].views
            else self.get_views(best_result[0].url)
            for best_result in best_results
        ]

        # print(f"# views_data: {views_data}")

        best_result_index = views_data.index(max(views_data))
        best_result = best_results[best_result_index]

        # print(f"# get_best_match URL - {best_result[0]} - sorted by views")
        return best_result[0].url, best_result[1]

    def get_download_metadata(self, url: str, download: bool = False) -> Dict:
        """
        Get metadata for a download using yt-dlp.

        ### Arguments
        - url: The url to get metadata for.

        ### Returns
        - A dictionary containing the metadata.
        """

        try:
            data = self.audio_handler.extract_info(url, download=download)

            if data:
                return data
        except Exception as exception:
            raise AudioProviderError(f"YT-DLP download error - {url}") from exception

        raise AudioProviderError(f"No metadata found for the provided url {url}")

    @property
    def name(self) -> str:
        """
        Get the name of the provider.

        ### Returns
        - The name of the provider.
        """

        return self.__class__.__name__

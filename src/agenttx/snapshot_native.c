/* Optional native batch operations for the snapshot-only storage API.
 * Build: cc -O3 -fPIC -shared -pthread -o _snapshot_native.so snapshot_native.c
 */
#include <errno.h>
#include <dirent.h>
#include <fcntl.h>
#include <pthread.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#ifndef AGENTTX_UNLINK_THREADS
#define AGENTTX_UNLINK_THREADS 4
#endif

struct unlink_job {
    int directory;
    const char *names;
    size_t begin, end, failed;
    int error;
};

static void *unlink_part(void *argument) {
    struct unlink_job *job = argument;
    size_t offset = job->begin;
    while (offset < job->end) {
        const char *end = memchr(job->names + offset, '\0', job->end - offset);
        if (end == NULL || end == job->names + offset) {
            job->failed = offset;
            job->error = EINVAL;
            return NULL;
        }
        if (unlinkat(job->directory, job->names + offset, 0) != 0 && errno != ENOENT) {
            job->failed = offset;
            job->error = errno;
            return NULL;
        }
        offset = (size_t)(end - job->names) + 1;
    }
    return NULL;
}

/* Consume NUL-separated basenames relative to an already opened directory.
 * Return the earliest error and its byte offset. Python handles directory
 * whiteouts and propagates other errors. Resuming after a directory error may
 * revisit names completed by another worker; ENOENT is a successful deletion.
 */
int agenttx_unlink_many(int directory, const char *names, size_t length,
                       size_t *consumed) {
    unsigned count = length >= 32768 ? AGENTTX_UNLINK_THREADS : 1;
    struct unlink_job jobs[AGENTTX_UNLINK_THREADS];
    pthread_t threads[AGENTTX_UNLINK_THREADS];
    int started[AGENTTX_UNLINK_THREADS] = {0};
    size_t begin = 0;
    for (unsigned i = 0; i < count; ++i) {
        size_t end = i+1 == count ? length : length*(i+1)/count;
        while (end < length && (end == 0 || names[end-1] != '\0')) ++end;
        jobs[i] = (struct unlink_job){directory, names, begin, end, 0, 0};
        begin = end;
        if (count > 1 && pthread_create(&threads[i], NULL, unlink_part, &jobs[i]) == 0) {
            started[i] = 1;
        } else {
            unlink_part(&jobs[i]);
        }
    }
    for (unsigned i = 0; i < count; ++i)
        if (started[i]) pthread_join(threads[i], NULL);
    *consumed = length;
    int error = 0;
    for (unsigned i = 0; i < count; ++i) {
        if (jobs[i].error && jobs[i].failed < *consumed) {
            *consumed = jobs[i].failed;
            error = jobs[i].error;
        }
    }
    return error;
}

/* Preflight an entire deletion-only layer before changing its destination.
 * Native OverlayFS shares whiteout inodes, so inspect each distinct inode
 * once. Mixed layers use the general Python publisher without changes here.
 */
int agenttx_delete_only(int source, int destination) {
    int fd = openat(source, ".", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (fd < 0) return errno;
    DIR *directory = fdopendir(fd);
    if (directory == NULL) { int error = errno; close(fd); return error; }
    size_t length = 0, capacity = 4096;
    char *names = malloc(capacity);
    if (names == NULL) { closedir(directory); return ENOMEM; }
    ino_t verified = 0;
    int error = 0;
    struct dirent *entry;
    for (;;) {
        errno = 0;
        entry = readdir(directory);
        if (entry == NULL) { error = errno; break; }
        if (strcmp(entry->d_name, ".") == 0 || strcmp(entry->d_name, "..") == 0) continue;
        if (entry->d_type != DT_CHR) { error = ENOTSUP; break; }
        if (entry->d_ino != verified) {
            struct stat metadata;
            if (fstatat(source, entry->d_name, &metadata, AT_SYMLINK_NOFOLLOW) != 0) {
                error = errno; break;
            }
            if (!S_ISCHR(metadata.st_mode) || metadata.st_rdev != 0) { error = ENOTSUP; break; }
            verified = entry->d_ino;
        }
        size_t size = strlen(entry->d_name)+1;
        if (length+size > capacity) {
            capacity = (length+size)*2;
            char *larger = realloc(names, capacity);
            if (larger == NULL) { error = ENOMEM; break; }
            names = larger;
        }
        memcpy(names+length, entry->d_name, size);
        length += size;
    }
    closedir(directory);
    if (error == 0) {
        size_t consumed;
        error = agenttx_unlink_many(destination, names, length, &consumed);
    }
    free(names);
    return error;
}
